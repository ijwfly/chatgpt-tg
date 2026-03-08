import asyncio

import pytest

from app.openai_helpers.llm_client_factory import LLMClientFactory
from tests.helpers.mock_llm_client import MockLLMClient
from tests.helpers.telegram_factory import make_text_message, make_command_message, make_callback_query
from tests.helpers.bot_spy import BotSpy


class TestSettings:

    async def test_settings_command_shows_menu(self, bot_app):
        """/settings sends a message with 'Settings:' text."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)

        user_id = 77770

        update = make_command_message('settings', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.05)

        spy.assert_sent_text_contains("Settings:")

    async def test_toggle_setting_updates_db(self, bot_app):
        """Toggling streaming_answers via callback query updates the DB."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)

        user_id = 77771

        # Create user first
        mock_llm = MockLLMClient()
        mock_llm.add_response("Hello!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Hi', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.1)

        # Check initial value
        user = await telegram_bot.db.get_user(user_id)
        initial_streaming = user.streaming_answers

        # Send /settings to get the settings message
        update_settings = make_command_message('settings', user_id=user_id)
        await dp.process_update(update_settings)
        await asyncio.sleep(0.05)

        # Get the message_id from the settings sendMessage call
        # Find the sendMessage that contains "Settings:"
        sent = spy.get_sent_messages()
        settings_msg = None
        for msg in sent:
            if 'Settings:' in msg.get('text', ''):
                settings_msg = msg
                break
        assert settings_msg is not None, "Settings message not found"

        # We need the message_id from the mock return, which is in the
        # Bot.request return values. Since we can't easily get them from spy,
        # we look at the mock calls. The mock_request returns dicts with message_id.
        # Let's get it from the call results.
        settings_call = None
        for call in spy.get_all_calls():
            args, kwargs = call
            if args and args[0] == 'sendMessage':
                data = args[1] if len(args) > 1 else kwargs.get('data', {})
                if 'Settings:' in data.get('text', ''):
                    # Get the return value - it's the result of the awaited call
                    settings_call = call
                    break

        # The message_id comes from the mock return value. Since we can't easily
        # access return values from AsyncMock side_effect, we'll use the DB to
        # find the message_id. But settings message isn't stored in DB.
        # Instead, let's use a known message_id from the mock counter.
        # The mock returns incrementing IDs starting from 5000+.
        # We need to send callback_query with any valid message_id that the bot
        # will process. The callback handler only needs the message_id for
        # edit_message_reply_markup. Let's just use a reasonable ID.
        # Actually, let's check what IDs the mock returned by examining request calls.

        # Simpler approach: just send a callback_query with an arbitrary message_id.
        # The settings handler uses callback_query.message.message_id to edit markup,
        # which is what we pass in make_callback_query.
        callback_msg_id = 9999  # Arbitrary, mock will accept any

        update_callback = make_callback_query(
            data='settings.streaming_answers',
            message_id=callback_msg_id,
            user_id=user_id,
        )
        await dp.process_update(update_callback)
        await asyncio.sleep(0.05)

        # Verify streaming_answers was toggled
        user = await telegram_bot.db.get_user(user_id)
        assert user.streaming_answers != initial_streaming, \
            f"Expected streaming_answers to be toggled from {initial_streaming}"

    async def test_hide_settings_deletes_message(self, bot_app):
        """settings.hide callback deletes the settings message."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)

        user_id = 77772

        # Send hide callback
        callback_msg_id = 8888

        update_callback = make_callback_query(
            data='settings.hide',
            message_id=callback_msg_id,
            user_id=user_id,
        )
        await dp.process_update(update_callback)
        await asyncio.sleep(0.05)

        # Verify deleteMessage was called
        delete_calls = spy.get_calls_for_method('deleteMessage')
        assert len(delete_calls) > 0, "Expected deleteMessage call for settings.hide"
