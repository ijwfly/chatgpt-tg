import asyncio
import json

import pytest

import settings
from app.openai_helpers.llm_client_factory import LLMClientFactory
from tests.helpers.mock_llm_client import MockLLMClient
from tests.helpers.telegram_factory import make_text_message
from tests.helpers.bot_spy import BotSpy


class TestFunctionCalling:

    async def test_tool_call_executes_and_returns_to_llm(self, bot_app):
        """Tool call response is passed back to LLM, which produces final answer."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)

        user_id = 44444

        # First message: create user
        mock_llm = MockLLMClient()
        mock_llm.add_response("Hello!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Hi', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.1)

        # Enable functions and system_prompt_settings
        user = await telegram_bot.db.get_user(user_id)
        user.use_functions = True
        user.system_prompt_settings_enabled = True
        await telegram_bot.db.update_user(user)

        # Second message: LLM returns tool call, then final response
        mock_llm2 = MockLLMClient()
        mock_llm2.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_1',
                'function': {
                    'name': 'save_user_settings',
                    'arguments': json.dumps({'settings_text': 'Name: Test'}),
                },
            }],
        )
        mock_llm2.add_response(content="Settings saved!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm2

        update2 = make_text_message('Save my name as Test', user_id=user_id)
        await dp.process_update(update2)
        await asyncio.sleep(0.2)

        spy.assert_sent_text_contains("Settings saved!")

        # Verify DB was updated by the function
        user = await telegram_bot.db.get_user(user_id)
        assert user.system_prompt_settings == 'Name: Test'

    async def test_function_call_verbose_shows_details(self, bot_app):
        """With function_call_verbose=True, bot sends function call details."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)

        user_id = 44445

        # Create user
        mock_llm = MockLLMClient()
        mock_llm.add_response("Hello!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Hi', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.1)

        # Enable functions, settings, and verbose mode
        user = await telegram_bot.db.get_user(user_id)
        user.use_functions = True
        user.system_prompt_settings_enabled = True
        user.function_call_verbose = True
        await telegram_bot.db.update_user(user)

        # Tool call + final response
        mock_llm2 = MockLLMClient()
        mock_llm2.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_2',
                'function': {
                    'name': 'save_user_settings',
                    'arguments': json.dumps({'settings_text': 'Name: Verbose'}),
                },
            }],
        )
        mock_llm2.add_response(content="Done!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm2

        update2 = make_text_message('Save my name as Verbose', user_id=user_id)
        await dp.process_update(update2)
        await asyncio.sleep(0.2)

        spy.assert_sent_text_contains("Function call: save_user_settings")

    async def test_successive_function_call_limit(self, bot_app):
        """Exceeding SUCCESSIVE_FUNCTION_CALLS_LIMIT produces an error."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)

        user_id = 44446

        # Create user
        mock_llm = MockLLMClient()
        mock_llm.add_response("Hello!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Hi', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.1)

        user = await telegram_bot.db.get_user(user_id)
        user.use_functions = True
        user.system_prompt_settings_enabled = True
        await telegram_bot.db.update_user(user)

        # Queue more tool call responses than the limit allows
        mock_llm2 = MockLLMClient()
        for i in range(settings.SUCCESSIVE_FUNCTION_CALLS_LIMIT + 1):
            mock_llm2.add_response(
                content=None,
                tool_calls=[{
                    'id': f'call_{i}',
                    'function': {
                        'name': 'save_user_settings',
                        'arguments': json.dumps({'settings_text': f'Iteration {i}'}),
                    },
                }],
            )
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm2

        update2 = make_text_message('Loop forever', user_id=user_id)
        with pytest.raises(ValueError):
            await dp.process_update(update2)
        await asyncio.sleep(0.3)

        spy.assert_sent_text_contains("Something went wrong")
