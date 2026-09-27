"""
An assistant message with `tool_calls` that never got its results (interrupted turn) must not break
the dialog branch: the runtime closes such calls itself, and already broken histories are repaired on load.
"""
import asyncio
import json
from unittest.mock import patch

import pytest

from app.context.tool_call_repair import INTERRUPTED_TOOL_CALL_RESULT
from app.openai_helpers.chatgpt import DialogMessage, FunctionCall, ToolCall
from app.openai_helpers.llm_client_factory import LLMClientFactory
from tests.helpers.bot_spy import BotSpy
from tests.helpers.mock_llm_client import MockLLMClient
from tests.helpers.telegram_factory import make_text_message


async def _create_user(telegram_bot, dp, user_id, agent_mode=False):
    mock_llm = MockLLMClient()
    mock_llm.add_response('Hello!')
    LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

    await dp.feed_update(telegram_bot.bot, make_text_message('Hi', user_id=user_id))
    await asyncio.sleep(0.1)

    user = await telegram_bot.db.get_user(user_id)
    user.use_functions = True
    user.agent_mode = agent_mode
    await telegram_bot.db.update_user(user)
    return user


def _plan_tool_call(tool_call_id):
    return {
        'id': tool_call_id,
        'function': {
            'name': 'CreatePlan',
            'arguments': json.dumps({'title': 'Plan', 'steps': ['one']}),
        },
    }


def _find_tool_calls_message(messages):
    return next(i for i, m in enumerate(messages) if m.get('tool_calls'))


class TestBrokenHistoryRepairedOnLoad:

    async def test_dangling_tool_calls_in_db_get_synthetic_results(self, bot_app):
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 71001
        user = await _create_user(telegram_bot, dp, user_id)

        # the dialog ends with a tool_calls message whose results were never saved
        last = await telegram_bot.db.get_last_message(user.id, user_id)
        previous = await telegram_bot.db.get_messages_by_ids(last.previous_message_ids)
        dangling = DialogMessage(role='assistant', content=None, tool_calls=[
            ToolCall(id='read_file_0', type='function', function=FunctionCall(name='read_file', arguments='{"path": "a.pdf"}')),
            ToolCall(id='read_file_1', type='function', function=FunctionCall(name='read_file', arguments='{"path": "b.pdf"}')),
        ])
        await telegram_bot.db.create_message(user.id, user_id, -1, dangling, previous + [last])

        mock_llm = MockLLMClient()
        mock_llm.add_response('Here is the transcript.')
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        await dp.feed_update(mock_bot, make_text_message('Help me decode it', user_id=user_id))
        await asyncio.sleep(0.3)

        spy.assert_sent_text_contains('Here is the transcript.')
        messages = mock_llm.calls[-1]['messages']
        i = _find_tool_calls_message(messages)
        assert [m['tool_call_id'] for m in messages[i + 1:i + 3]] == ['read_file_0', 'read_file_1']
        assert all(m['role'] == 'tool' and m['content'] == INTERRUPTED_TOOL_CALL_RESULT for m in messages[i + 1:i + 3])
        assert messages[i + 3]['role'] == 'user'


class TestInterruptedTurnClosesToolCalls:

    async def test_transport_failure_during_tool_call_leaves_a_result_in_db(self, bot_app):
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 71002
        user = await _create_user(telegram_bot, dp, user_id, agent_mode=True)

        mock_llm = MockLLMClient()
        mock_llm.add_response(content=None, tool_calls=[_plan_tool_call('call_interrupted')])
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        # the adapter blows up while showing the tool hint: the runtime generator is closed mid-tool
        with patch('app.bot.telegram_runtime_adapter._format_hint', side_effect=RuntimeError('transport down')):
            with pytest.raises(RuntimeError):
                await dp.feed_update(mock_bot, make_text_message('Make a plan', user_id=user_id))
            await asyncio.sleep(0.3)

        spy.assert_sent_text_contains('Something went wrong')

        last = await telegram_bot.db.get_last_message(user.id, user_id)
        assert last.message.role == 'tool'
        assert last.message.tool_call_id == 'call_interrupted'
        assert last.message.content == INTERRUPTED_TOOL_CALL_RESULT

        # the branch is usable again: the next turn sends a valid tool_calls/tool sequence
        mock_llm.add_response('Back to normal.')
        await dp.feed_update(mock_bot, make_text_message('Are you there?', user_id=user_id))
        await asyncio.sleep(0.3)

        spy.assert_sent_text_contains('Back to normal.')
        messages = mock_llm.calls[-1]['messages']
        i = _find_tool_calls_message(messages)
        assert messages[i + 1]['role'] == 'tool'
        assert messages[i + 1]['tool_call_id'] == 'call_interrupted'

    async def test_completed_tool_result_is_saved_before_the_event_reaches_the_adapter(self, bot_app):
        telegram_bot, dp, mock_bot = bot_app
        user_id = 71003
        user = await _create_user(telegram_bot, dp, user_id, agent_mode=True)
        user.function_call_verbose = True
        await telegram_bot.db.update_user(user)

        mock_llm = MockLLMClient()
        mock_llm.add_response(content=None, tool_calls=[_plan_tool_call('call_done')])
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        # verbose output of the finished call fails in the adapter
        with patch('app.bot.telegram_runtime_adapter.send_telegram_message', side_effect=RuntimeError('transport down')):
            with pytest.raises(RuntimeError):
                await dp.feed_update(mock_bot, make_text_message('Make a plan', user_id=user_id))
            await asyncio.sleep(0.3)

        last = await telegram_bot.db.get_last_message(user.id, user_id)
        assert last.message.role == 'tool'
        assert last.message.tool_call_id == 'call_done'
        assert last.message.content != INTERRUPTED_TOOL_CALL_RESULT
