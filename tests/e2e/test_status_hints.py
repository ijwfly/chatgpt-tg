"""The status hint shown while a tool runs must say what exactly is happening."""

import asyncio
import json

import pytest

import settings
from app.openai_helpers.llm_client_factory import LLMClientFactory
from app.web.tavily_client import TavilyClient
from tests.helpers.bot_spy import BotSpy
from tests.helpers.fake_sandbox import FakeSandboxClient, patch_sandbox_client
from tests.helpers.mock_llm_client import MockLLMClient
from tests.helpers.telegram_factory import make_text_message


@pytest.fixture(autouse=True)
def enable_sandbox():
    old = settings.ENABLE_BASH_SANDBOX
    settings.ENABLE_BASH_SANDBOX = True
    yield
    settings.ENABLE_BASH_SANDBOX = old


@pytest.fixture(autouse=True)
def fake_sandbox_client():
    with patch_sandbox_client() as client:
        yield client


async def _create_agent_user(telegram_bot, dp, user_id):
    mock_llm = MockLLMClient()
    mock_llm.add_response("Hello!")
    LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

    await dp.feed_update(telegram_bot.bot, make_text_message('Hi', user_id=user_id))
    await asyncio.sleep(0.1)

    user = await telegram_bot.db.get_user(user_id)
    user.agent_mode = True
    user.use_functions = True
    await telegram_bot.db.update_user(user)
    return user


def _tool_call(name, arguments, call_id='call_1'):
    return {'id': call_id, 'function': {'name': name, 'arguments': json.dumps(arguments)}}


class TestStatusHints:

    async def test_bash_hint_shows_the_command(self, bot_app):
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 96001

        await _create_agent_user(telegram_bot, dp, user_id)

        mock_llm = MockLLMClient()
        mock_llm.add_response(content=None, tool_calls=[
            _tool_call('bash_exec', {'command': 'ls -la /workspace', 'timeout': 60}),
        ])
        mock_llm.add_response(content="Listed the workspace.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        await dp.feed_update(mock_bot, make_text_message('what is in my workspace?', user_id=user_id))
        await asyncio.sleep(0.3)

        spy.assert_shown_text_contains('Running bash command: ls -la /workspace')
        spy.assert_sent_text_contains('Listed the workspace.')

    async def test_long_command_is_truncated_in_the_hint(self, bot_app):
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 96002

        await _create_agent_user(telegram_bot, dp, user_id)

        command = 'python3 scripts/process.py ' + 'a' * 200
        mock_llm = MockLLMClient()
        mock_llm.add_response(content=None, tool_calls=[_tool_call('bash_exec', {'command': command})])
        mock_llm.add_response(content="Done.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        await dp.feed_update(mock_bot, make_text_message('run it', user_id=user_id))
        await asyncio.sleep(0.3)

        hints = [t for t in spy.get_all_shown_texts() if t.startswith('Running bash command:')]
        assert hints, f'no bash hint shown, got: {spy.get_all_shown_texts()}'
        assert hints[0].endswith('…')
        assert len(hints[0]) < len(command)

    async def test_hint_with_markdown_characters_is_escaped(self, bot_app):
        """A path full of markdown specials must not break rich markup."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 96003

        await _create_agent_user(telegram_bot, dp, user_id)

        mock_llm = MockLLMClient()
        mock_llm.add_response(content=None, tool_calls=[
            _tool_call('read_file', {'path': 'reports/my_file[1]*.md'}),
        ])
        mock_llm.add_response(content="Read it.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        await dp.feed_update(mock_bot, make_text_message('read my file', user_id=user_id))
        await asyncio.sleep(0.3)

        spy.assert_shown_text_contains(r'Reading file: reports/my\_file\[1\]\*.md')
        spy.assert_sent_text_contains('Read it.')

    async def test_web_search_hint_shows_the_query(self, bot_app, monkeypatch):
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 96004
        monkeypatch.setattr(settings, 'ENABLE_WEB_AGENTS', True)

        await _create_agent_user(telegram_bot, dp, user_id)

        async def fake_search(self, query, max_results=5):
            return {'results': [{'title': 'T', 'url': 'https://example.com',
                                 'content': 'Answer.', 'score': 0.9}]}

        monkeypatch.setattr(TavilyClient, 'search', fake_search)

        mock_llm = MockLLMClient()
        mock_llm.add_response(content=None, tool_calls=[
            _tool_call('web_search_agent', {'query': 'latest python version'}),
        ])
        mock_llm.add_response(content=None, tool_calls=[
            _tool_call('tavily_search', {'query': 'latest python version'}, call_id='call_sub'),
        ])
        mock_llm.add_response(content="Python 3.13.")
        mock_llm.add_response(content="Python 3.13 is the latest.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        await dp.feed_update(mock_bot, make_text_message('what is the latest python?', user_id=user_id))
        await asyncio.sleep(0.5)

        spy.assert_shown_text_contains('Searching the web: latest python version')

    async def test_tool_without_detail_keeps_its_plain_hint(self, bot_app):
        """send_file_to_chat has a path, WaitTask has nothing — the latter stays as before."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 96005

        await _create_agent_user(telegram_bot, dp, user_id)

        mock_llm = MockLLMClient()
        mock_llm.add_response(content=None, tool_calls=[_tool_call('WaitTask', {})])
        mock_llm.add_response(content="Nothing to wait for.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        await dp.feed_update(mock_bot, make_text_message('wait for tasks', user_id=user_id))
        await asyncio.sleep(0.3)

        shown = spy.get_all_shown_texts()
        assert any(t.strip() == 'Waiting for background tasks...' for t in shown), \
            f'expected the plain hint, got: {shown}'
