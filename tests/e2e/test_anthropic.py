"""Anthropic (Claude) path: AnthropicChatGPT request/response conversion against the installed anthropic SDK types."""
import asyncio
from types import SimpleNamespace

import pytest

import settings
from app.llm_models import LLModel, get_models
from app.openai_helpers.llm_client_factory import LLMClientFactory
from tests.helpers.bot_spy import BotSpy
from tests.helpers.mock_anthropic_client import MockAnthropicClient
from tests.helpers.mock_llm_client import MockLLMClient
from tests.helpers.telegram_factory import make_callback_query, make_text_message

CLAUDE = LLModel.ANTHROPIC_CLAUDE_35_SONNET


@pytest.fixture
def anthropic_enabled():
    """Claude models exist only when ANTHROPIC_TOKEN is set (conftest clears it)."""
    old = settings.ANTHROPIC_TOKEN
    settings.ANTHROPIC_TOKEN = 'test-anthropic-key'
    get_models.cache_clear()
    yield
    settings.ANTHROPIC_TOKEN = old
    get_models.cache_clear()


async def _create_claude_user(telegram_bot, dp, user_id, **fields):
    """Creates the user with a regular model, then switches them to Claude."""
    mock_llm = MockLLMClient()
    mock_llm.add_response('Hello!')
    LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm
    await dp.feed_update(telegram_bot.bot, make_text_message('Hi', user_id=user_id))
    await asyncio.sleep(0.1)

    user = await telegram_bot.db.get_user(user_id)
    user.current_model = CLAUDE
    for key, value in fields.items():
        setattr(user, key, value)
    await telegram_bot.db.update_user(user)
    return user


class TestAnthropic:

    async def test_sync_response(self, anthropic_enabled, bot_app):
        """Non-streaming Message → text answer; the request is in Anthropic format (system split out, roles mapped)."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 61001
        await _create_claude_user(telegram_bot, dp, user_id)

        client = MockAnthropicClient()
        client.add_response(text='Claude says hi.', input_tokens=12, output_tokens=7)
        LLMClientFactory._model_clients[CLAUDE] = client

        await dp.feed_update(mock_bot, make_text_message('Hello Claude', user_id=user_id))
        await asyncio.sleep(0.2)

        spy.assert_sent_text_contains('Claude says hi.')

        call = client.calls[-1]
        assert call['model'] == CLAUDE
        # AnthropicChatGPT.create_context puts the system prompt first and converts content to Anthropic parts
        assert call['messages'][0]['role'] == 'system'
        assert call['messages'][-1]['role'] == 'user'
        assert call['messages'][-1]['content'][-1] == {'type': 'text', 'text': 'Hello Claude'}
        assert 'tools' not in call['additional_fields']  # functions are off for this user

        usages = await telegram_bot.db.get_user_current_month_completion_usage((await telegram_bot.db.get_user(user_id)).id)
        claude_usage = [u for u in usages if u.model == CLAUDE]
        assert claude_usage and claude_usage[0].prompt_tokens == 12 and claude_usage[0].completion_tokens == 7

    async def test_streaming_response(self, anthropic_enabled, bot_app):
        """Streamed text deltas are accumulated into the final answer; usage comes from message_start + message_delta."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 61002
        await _create_claude_user(telegram_bot, dp, user_id, streaming_answers=True)

        client = MockAnthropicClient()
        client.add_streaming_response(
            text_chunks=['Streamed ', 'answer ', 'from Claude, ', 'long enough to be rendered while streaming.'],
            input_tokens=30, output_tokens=15,
        )
        LLMClientFactory._model_clients[CLAUDE] = client

        await dp.feed_update(mock_bot, make_text_message('Stream please', user_id=user_id))
        await asyncio.sleep(0.3)

        assert client.calls[-1]['additional_fields'].get('stream') is True
        spy.assert_sent_text_contains('Streamed answer from Claude, long enough to be rendered while streaming.')

        usages = await telegram_bot.db.get_user_current_month_completion_usage((await telegram_bot.db.get_user(user_id)).id)
        claude_usage = [u for u in usages if u.model == CLAUDE]
        assert claude_usage and claude_usage[0].prompt_tokens == 30 and claude_usage[0].completion_tokens == 15

    async def test_streaming_tool_use_round_trip(self, anthropic_enabled, bot_app):
        """A streamed tool_use block (input_json_delta chunks) is executed and its result goes back as tool_result."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 61003
        await _create_claude_user(
            telegram_bot, dp, user_id, streaming_answers=True, use_functions=True, system_prompt_settings_enabled=True,
        )

        client = MockAnthropicClient()
        client.add_streaming_response(
            tool_use={'id': 'toolu_01', 'name': 'save_user_settings', 'input': {'settings_text': 'Name: Claude Tester'}},
        )
        client.add_streaming_response(text_chunks=['Saved your settings, ', 'anything else I can do for you today?'])
        LLMClientFactory._model_clients[CLAUDE] = client

        await dp.feed_update(mock_bot, make_text_message('Remember my name is Claude Tester', user_id=user_id))
        await asyncio.sleep(0.4)

        spy.assert_sent_text_contains('Saved your settings')
        user = await telegram_bot.db.get_user(user_id)
        assert user.system_prompt_settings == 'Name: Claude Tester'

        # tools are sent in Anthropic format (input_schema instead of parameters)
        tools = client.calls[0]['additional_fields']['tools']
        assert tools and all('input_schema' in tool and 'parameters' not in tool for tool in tools)
        assert client.calls[0]['additional_fields']['tool_choice'] == {'type': 'auto'}

        # second request carries the assistant tool_use and the tool_result in Anthropic format
        assert len(client.calls) == 2
        messages = client.calls[1]['messages']
        tool_use_parts = [p for m in messages if m['role'] == 'assistant' for p in m['content'] if p['type'] == 'tool_use']
        assert tool_use_parts and tool_use_parts[-1]['id'] == 'toolu_01' \
            and tool_use_parts[-1]['input'] == {'settings_text': 'Name: Claude Tester'}
        tool_result_parts = [p for m in messages if m['role'] == 'user' for p in m['content'] if p['type'] == 'tool_result']
        assert tool_result_parts and tool_result_parts[-1]['tool_use_id'] == 'toolu_01'

    async def test_unknown_stream_event_is_ignored(self, anthropic_enabled, bot_app):
        """Event types this code base does not handle are skipped instead of aborting the response."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 61004
        await _create_claude_user(telegram_bot, dp, user_id, streaming_answers=True)

        client = MockAnthropicClient()
        client.add_streaming_response(
            text_chunks=['Still fine after an unknown event, ', 'the answer arrives in full.'],
            extra_event=SimpleNamespace(type='some_future_event_type', index=0),
        )
        LLMClientFactory._model_clients[CLAUDE] = client

        await dp.feed_update(mock_bot, make_text_message('Go', user_id=user_id))
        await asyncio.sleep(0.3)

        spy.assert_sent_text_contains('Still fine after an unknown event, the answer arrives in full.')
        assert not any('Something went wrong' in t for t in spy.get_all_sent_texts())


class TestAnthropicCancellation:

    async def test_stop_cancels_a_streamed_claude_answer(self, anthropic_enabled, bot_app):
        """The Anthropic stream honours the cancellation token: the partial answer is finalised, the rest is not read."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 61010
        await _create_claude_user(telegram_bot, dp, user_id, streaming_answers=True)

        client = MockAnthropicClient()
        client.add_streaming_response(
            text_chunks=[f'chunk {i} of a slow Claude answer that keeps going, ' for i in range(40)], chunk_delay=0.02,
        )
        LLMClientFactory._model_clients[CLAUDE] = client

        turn = asyncio.create_task(dp.feed_update(mock_bot, make_text_message('Slow stream', user_id=user_id)))
        await asyncio.sleep(0.25)
        await dp.feed_update(mock_bot, make_callback_query('cancel.cancel', message_id=1, user_id=user_id))
        await asyncio.wait_for(turn, timeout=2)

        final = spy.get_all_shown_texts()[-1]
        assert 'chunk 0' in final and 'chunk 39' not in final, final

    async def test_stop_during_a_tool_use_does_not_execute_the_tool(self, anthropic_enabled, bot_app):
        """A tool_use block cut off mid-stream has no usable input; it must be dropped, not executed."""
        telegram_bot, dp, mock_bot = bot_app
        user_id = 61011
        await _create_claude_user(
            telegram_bot, dp, user_id, streaming_answers=True, use_functions=True, system_prompt_settings_enabled=True,
        )

        client = MockAnthropicClient()
        client.add_streaming_response(
            tool_use={'id': 'toolu_02', 'name': 'save_user_settings', 'input': {'settings_text': 'Name: Cut Off'}},
            chunk_delay=0.3,
        )
        client.add_streaming_response(text_chunks=['never requested'])
        LLMClientFactory._model_clients[CLAUDE] = client

        turn = asyncio.create_task(dp.feed_update(mock_bot, make_text_message('Remember my name', user_id=user_id)))
        await asyncio.sleep(0.15)
        await dp.feed_update(mock_bot, make_callback_query('cancel.cancel', message_id=1, user_id=user_id))
        await asyncio.wait_for(turn, timeout=3)

        user = await telegram_bot.db.get_user(user_id)
        assert user.system_prompt_settings != 'Name: Cut Off'
        assert len(client.calls) == 1  # no tool result round-trip
