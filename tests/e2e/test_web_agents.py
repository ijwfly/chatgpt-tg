import asyncio
import json

import settings
from app.openai_helpers.llm_client_factory import LLMClientFactory
from app.web.tavily_client import TavilyClient, TavilyError
from tests.helpers.mock_llm_client import MockLLMClient
from tests.helpers.telegram_factory import make_text_message
from tests.helpers.bot_spy import BotSpy


async def _create_user_with_functions(telegram_bot, dp, user_id):
    """Create a user via a first message and enable functions."""
    mock_llm = MockLLMClient()
    mock_llm.add_response("Hello!")
    LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

    update = make_text_message('Hi', user_id=user_id)
    await dp.process_update(update)
    await asyncio.sleep(0.1)

    user = await telegram_bot.db.get_user(user_id)
    user.use_functions = True
    await telegram_bot.db.update_user(user)


class TestWebAgents:

    async def test_web_search_agent(self, bot_app, db_pool, monkeypatch):
        """Main LLM calls web_search_agent; the sub-agent searches via tavily_search
        and returns a synthesized answer; sub-agent LLM usage is billed."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        monkeypatch.setattr(settings, 'ENABLE_WEB_AGENTS', True)

        user_id = 55551
        await _create_user_with_functions(telegram_bot, dp, user_id)

        search_calls = []

        async def fake_search(self, query, max_results=5):
            search_calls.append(query)
            return {'results': [
                {'title': 'Python 3.13 released', 'url': 'https://example.com/py313',
                 'content': 'Python 3.13 was released in October 2024.', 'score': 0.99},
            ]}

        monkeypatch.setattr(TavilyClient, 'search', fake_search)

        mock_llm = MockLLMClient()
        # 1) main LLM -> tool call web_search_agent
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_ws1',
                'function': {
                    'name': 'web_search_agent',
                    'arguments': json.dumps({'query': 'latest Python version'}),
                },
            }],
        )
        # 2) sub-agent -> tool call tavily_search
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_tv1',
                'function': {
                    'name': 'tavily_search',
                    'arguments': json.dumps({'query': 'latest Python version', 'max_results': 5}),
                },
            }],
        )
        # 3) sub-agent -> final answer with sources
        mock_llm.add_response("Latest is Python 3.13.\nSources:\n- https://example.com/py313")
        # 4) main LLM -> final answer to the user
        mock_llm.add_response("The latest Python version is 3.13.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('What is the latest Python version?', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.5)

        assert search_calls == ['latest Python version']
        spy.assert_sent_text_contains("The latest Python version is 3.13.")

        # Sub-agent tool result was passed back to the sub-agent LLM
        sub_agent_final_call = mock_llm.calls[2]
        assert 'https://example.com/py313' in json.dumps(sub_agent_final_call['messages'])

        # Billing: 1 (first message) + 1 (main tool call) + 2 (sub-agent) + 1 (main final)
        usage_count = await db_pool.fetchval('SELECT COUNT(*) FROM chatgpttg.completion_usage')
        assert usage_count == 5

    async def test_web_scraper_agent(self, bot_app, monkeypatch):
        """Main LLM calls web_scraper_agent; the sub-agent extracts the page
        via tavily_extract and returns a summary."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        monkeypatch.setattr(settings, 'ENABLE_WEB_AGENTS', True)

        user_id = 55552
        await _create_user_with_functions(telegram_bot, dp, user_id)

        extract_calls = []

        async def fake_extract(self, urls):
            extract_calls.append(urls)
            return {
                'results': [{'url': urls[0], 'raw_content': 'Example Domain. This domain is for use in examples.'}],
                'failed_results': [],
            }

        monkeypatch.setattr(TavilyClient, 'extract', fake_extract)

        mock_llm = MockLLMClient()
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_sc1',
                'function': {
                    'name': 'web_scraper_agent',
                    'arguments': json.dumps({'url': 'https://example.com', 'task': 'summarize the page'}),
                },
            }],
        )
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_tv2',
                'function': {
                    'name': 'tavily_extract',
                    'arguments': json.dumps({'urls': ['https://example.com']}),
                },
            }],
        )
        mock_llm.add_response("The page is a domain used in examples. Source: https://example.com")
        mock_llm.add_response("Summary: example.com is a domain reserved for examples.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Summarize https://example.com', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.5)

        assert extract_calls == [['https://example.com']]
        spy.assert_sent_text_contains("Summary: example.com is a domain reserved for examples.")

    async def test_tavily_error_does_not_crash(self, bot_app, monkeypatch):
        """TavilyError inside the sub-agent becomes a tool result string, the bot answers normally."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        monkeypatch.setattr(settings, 'ENABLE_WEB_AGENTS', True)

        user_id = 55553
        await _create_user_with_functions(telegram_bot, dp, user_id)

        async def failing_search(self, query, max_results=5):
            raise TavilyError('invalid api key')

        monkeypatch.setattr(TavilyClient, 'search', failing_search)

        mock_llm = MockLLMClient()
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_ws2',
                'function': {
                    'name': 'web_search_agent',
                    'arguments': json.dumps({'query': 'anything'}),
                },
            }],
        )
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_tv3',
                'function': {
                    'name': 'tavily_search',
                    'arguments': json.dumps({'query': 'anything'}),
                },
            }],
        )
        mock_llm.add_response("I could not search the web due to an error.")
        mock_llm.add_response("Sorry, web search is unavailable right now.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Search for anything', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.5)

        # The error reached the sub-agent LLM as a tool result string
        sub_agent_final_call = mock_llm.calls[2]
        assert 'invalid api key' in json.dumps(sub_agent_final_call['messages'])
        spy.assert_sent_text_contains("Sorry, web search is unavailable right now.")

    async def test_web_agent_model_setting(self, bot_app, db_pool, monkeypatch):
        """WEB_AGENT_MODEL routes the sub-agent to its own model, main dialog stays on the user's model."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        monkeypatch.setattr(settings, 'ENABLE_WEB_AGENTS', True)
        monkeypatch.setattr(settings, 'WEB_AGENT_MODEL', 'gpt-4.1-mini')

        user_id = 55554
        await _create_user_with_functions(telegram_bot, dp, user_id)

        async def fake_search(self, query, max_results=5):
            return {'results': [
                {'title': 'Result', 'url': 'https://example.com/r', 'content': 'Some content.', 'score': 0.9},
            ]}

        monkeypatch.setattr(TavilyClient, 'search', fake_search)

        main_llm = MockLLMClient()
        main_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_ws3',
                'function': {
                    'name': 'web_search_agent',
                    'arguments': json.dumps({'query': 'anything'}),
                },
            }],
        )
        main_llm.add_response("Done.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = main_llm

        sub_llm = MockLLMClient()
        sub_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_tv4',
                'function': {
                    'name': 'tavily_search',
                    'arguments': json.dumps({'query': 'anything'}),
                },
            }],
        )
        sub_llm.add_response("Found it.\nSources:\n- https://example.com/r")
        LLMClientFactory._model_clients['gpt-4.1-mini'] = sub_llm

        update = make_text_message('Search for anything', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.5)

        assert [call['model'] for call in sub_llm.calls] == ['gpt-4.1-mini', 'gpt-4.1-mini']
        spy.assert_sent_text_contains("Done.")

        # Sub-agent usage is billed under the sub-agent model
        models = await db_pool.fetch('SELECT model FROM chatgpttg.completion_usage')
        assert sum(1 for row in models if row['model'] == 'gpt-4.1-mini') == 2

    async def test_web_agent_model_fallback(self, bot_app, monkeypatch):
        """Unknown WEB_AGENT_MODEL falls back to the user's current model instead of crashing."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        monkeypatch.setattr(settings, 'ENABLE_WEB_AGENTS', True)
        monkeypatch.setattr(settings, 'WEB_AGENT_MODEL', 'nonexistent-model')

        user_id = 55555
        await _create_user_with_functions(telegram_bot, dp, user_id)

        async def fake_search(self, query, max_results=5):
            return {'results': [
                {'title': 'Result', 'url': 'https://example.com/r', 'content': 'Some content.', 'score': 0.9},
            ]}

        monkeypatch.setattr(TavilyClient, 'search', fake_search)

        mock_llm = MockLLMClient()
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_ws4',
                'function': {
                    'name': 'web_search_agent',
                    'arguments': json.dumps({'query': 'anything'}),
                },
            }],
        )
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_tv5',
                'function': {
                    'name': 'tavily_search',
                    'arguments': json.dumps({'query': 'anything'}),
                },
            }],
        )
        mock_llm.add_response("Found it.\nSources:\n- https://example.com/r")
        mock_llm.add_response("Here is your answer.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Search for anything', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.5)

        spy.assert_sent_text_contains("Here is your answer.")

    async def test_parallel_tool_calls(self, bot_app, monkeypatch):
        """Multiple tool calls in one sub-agent turn all execute, responses keep tool call order."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        monkeypatch.setattr(settings, 'ENABLE_WEB_AGENTS', True)

        user_id = 55556
        await _create_user_with_functions(telegram_bot, dp, user_id)

        async def fake_search(self, query, max_results=5):
            return {'results': [
                {'title': f'Result for {query}', 'url': f'https://example.com/{query}',
                 'content': f'Content about {query}.', 'score': 0.9},
            ]}

        monkeypatch.setattr(TavilyClient, 'search', fake_search)

        mock_llm = MockLLMClient()
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_ws5',
                'function': {
                    'name': 'web_search_agent',
                    'arguments': json.dumps({'query': 'compare A and B'}),
                },
            }],
        )
        # Sub-agent issues two searches in a single turn
        mock_llm.add_response(
            content=None,
            tool_calls=[
                {
                    'id': 'call_tv6',
                    'function': {
                        'name': 'tavily_search',
                        'arguments': json.dumps({'query': 'topicA'}),
                    },
                },
                {
                    'id': 'call_tv7',
                    'function': {
                        'name': 'tavily_search',
                        'arguments': json.dumps({'query': 'topicB'}),
                    },
                },
            ],
        )
        mock_llm.add_response("A vs B comparison.\nSources:\n- https://example.com/topicA\n- https://example.com/topicB")
        mock_llm.add_response("Comparison ready.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Compare A and B', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.5)

        spy.assert_sent_text_contains("Comparison ready.")

        # Both tool results reached the sub-agent, in tool call order
        sub_agent_final_messages = mock_llm.calls[2]['messages']
        tool_messages = [m for m in sub_agent_final_messages if m.get('role') == 'tool']
        assert [m['tool_call_id'] for m in tool_messages] == ['call_tv6', 'call_tv7']
        assert 'topicA' in tool_messages[0]['content']
        assert 'topicB' in tool_messages[1]['content']

    async def test_finalization_on_iteration_limit(self, bot_app, monkeypatch):
        """When the iteration limit is hit, the sub-agent gets one nudged finalization call
        and the user receives a real answer instead of 'stopped early'."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        monkeypatch.setattr(settings, 'ENABLE_WEB_AGENTS', True)
        monkeypatch.setattr(settings, 'WEB_AGENT_MAX_ITERATIONS', 1)

        user_id = 55557
        await _create_user_with_functions(telegram_bot, dp, user_id)

        async def fake_search(self, query, max_results=5):
            return {'results': [
                {'title': 'Result', 'url': 'https://example.com/r', 'content': 'Some content.', 'score': 0.9},
            ]}

        monkeypatch.setattr(TavilyClient, 'search', fake_search)

        mock_llm = MockLLMClient()
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_ws6',
                'function': {
                    'name': 'web_search_agent',
                    'arguments': json.dumps({'query': 'anything'}),
                },
            }],
        )
        # Sub-agent iteration 1 (the whole budget) — wants a tool call
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_tv8',
                'function': {
                    'name': 'tavily_search',
                    'arguments': json.dumps({'query': 'anything'}),
                },
            }],
        )
        # Finalization call — answers with text
        mock_llm.add_response("Best effort answer.\nSources:\n- https://example.com/r")
        mock_llm.add_response("Here is the answer.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Search for anything', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.5)

        spy.assert_sent_text_contains("Here is the answer.")

        # The finalization call saw the nudge message and the tool result
        finalization_messages = mock_llm.calls[2]['messages']
        assert 'tool call limit' in json.dumps(finalization_messages)

        # The main LLM got the real answer, not a partial-result marker
        main_final_messages = json.dumps(mock_llm.calls[3]['messages'])
        assert 'Best effort answer.' in main_final_messages
        assert 'stopped early' not in main_final_messages
