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
        await dp.feed_update(mock_bot, update)
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
        await dp.feed_update(mock_bot, update2)
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
        await dp.feed_update(mock_bot, update)
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
        await dp.feed_update(mock_bot, update2)
        await asyncio.sleep(0.2)

        spy.assert_sent_text_contains("Function call: save_user_settings")

    async def test_function_call_hint_is_overwritten_by_final_answer(self, bot_app):
        """With hints on, the hint and the final answer share one message: hint is shown via edit, then final overwrites it via edit."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)

        user_id = 44447

        mock_llm = MockLLMClient()
        mock_llm.add_response("Hello!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Hi', user_id=user_id)
        await dp.feed_update(mock_bot, update)
        await asyncio.sleep(0.1)

        user = await telegram_bot.db.get_user(user_id)
        user.use_functions = True
        user.system_prompt_settings_enabled = True
        # function_call_hints defaults to True; assert it explicitly
        assert user.function_call_hints is True
        await telegram_bot.db.update_user(user)

        mock_llm2 = MockLLMClient()
        mock_llm2.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_hint',
                'function': {
                    'name': 'save_user_settings',
                    'arguments': json.dumps({'settings_text': 'Name: Hint'}),
                },
            }],
        )
        mock_llm2.add_response(content="OK!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm2

        deletes_before = len(spy.get_calls_for_method('deleteMessage'))

        update2 = make_text_message('Save my name as Hint', user_id=user_id)
        await dp.feed_update(mock_bot, update2)
        await asyncio.sleep(0.2)

        # Hint message uses the function-specific status from SaveUserSettings (a draft in private chats)
        spy.assert_shown_text_contains("Saving user info...")
        # Final assistant text reaches the chat too
        spy.assert_sent_text_contains("OK!")

        # The hint must NOT be deleted — it gets edited into the final answer.
        deletes_after = len(spy.get_calls_for_method('deleteMessage'))
        assert deletes_after == deletes_before, (
            "Service message should be reused via edit, not deleted. "
            f"Before turn: {deletes_before}, after: {deletes_after}"
        )

    async def test_tool_only_response_does_not_delete_service_message(self, bot_app):
        """When LLM produces a tool_call without content, the service message must not flicker."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)

        user_id = 44449

        mock_llm = MockLLMClient()
        mock_llm.add_response("Hello!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Hi', user_id=user_id)
        await dp.feed_update(mock_bot, update)
        await asyncio.sleep(0.1)

        user = await telegram_bot.db.get_user(user_id)
        user.use_functions = True
        user.system_prompt_settings_enabled = True
        await telegram_bot.db.update_user(user)

        deletes_before = len(spy.get_calls_for_method('deleteMessage'))

        # Two consecutive tool calls before the final answer.
        mock_llm2 = MockLLMClient()
        for i, label in enumerate(('First', 'Second')):
            mock_llm2.add_response(
                content=None,
                tool_calls=[{
                    'id': f'call_chain_{i}',
                    'function': {
                        'name': 'save_user_settings',
                        'arguments': json.dumps({'settings_text': f'Name: {label}'}),
                    },
                }],
            )
        mock_llm2.add_response(content="Done!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm2

        update2 = make_text_message('Update twice', user_id=user_id)
        await dp.feed_update(mock_bot, update2)
        await asyncio.sleep(0.3)

        spy.assert_sent_text_contains("Done!")

        # Across the chained tool calls there must be no delete: the service
        # message is reused via edit between phases.
        deletes_after = len(spy.get_calls_for_method('deleteMessage'))
        assert deletes_after == deletes_before, (
            f"Expected no extra deleteMessage calls during chained tool calls, "
            f"got {deletes_after - deletes_before} new"
        )

    async def test_function_call_hints_disabled(self, bot_app):
        """With function_call_hints=False, no hint message is sent."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)

        user_id = 44448

        mock_llm = MockLLMClient()
        mock_llm.add_response("Hello!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Hi', user_id=user_id)
        await dp.feed_update(mock_bot, update)
        await asyncio.sleep(0.1)

        user = await telegram_bot.db.get_user(user_id)
        user.use_functions = True
        user.system_prompt_settings_enabled = True
        user.function_call_hints = False
        await telegram_bot.db.update_user(user)

        mock_llm2 = MockLLMClient()
        mock_llm2.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_no_hint',
                'function': {
                    'name': 'save_user_settings',
                    'arguments': json.dumps({'settings_text': 'Name: NoHint'}),
                },
            }],
        )
        mock_llm2.add_response(content="OK!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm2

        update2 = make_text_message('Save my name as NoHint', user_id=user_id)
        await dp.feed_update(mock_bot, update2)
        await asyncio.sleep(0.2)

        all_texts = spy.get_all_sent_texts() + spy.get_all_edited_texts()
        assert not any('Saving user info...' in t for t in all_texts), (
            f"Expected no hint message when hints disabled, got: {all_texts}"
        )

    async def test_long_final_response_splits_into_chunks(self, bot_app, monkeypatch):
        """Final response longer than the Telegram cutoff is split into multiple messages."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)

        user_id = 44450

        # rich messages allow 30000 chars; lower the cutoff so a ~6750-char answer splits into >= 2 chunks
        monkeypatch.setattr('app.bot.telegram_runtime_adapter.TELEGRAM_MESSAGE_LENGTH_CUTOFF', 4080)
        long_content = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 120

        mock_llm = MockLLMClient()
        mock_llm.add_response(long_content)
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Tell a long story', user_id=user_id)
        await dp.feed_update(mock_bot, update)
        await asyncio.sleep(0.2)

        sends = spy.get_sent_messages()
        long_sends = [m for m in sends if 'Lorem ipsum' in (m.get('rich_message') or {}).get('markdown', '')]
        assert len(long_sends) >= 2, (
            f"Expected long final to split into >= 2 messages, got {len(long_sends)}"
        )
        # No deletes — every chunk is a fresh send, the service message owns
        # the first chunk.
        assert len(spy.get_calls_for_method('deleteMessage')) == 0

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
        await dp.feed_update(mock_bot, update)
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
            await dp.feed_update(mock_bot, update2)
        await asyncio.sleep(0.3)

        spy.assert_sent_text_contains("Something went wrong")
