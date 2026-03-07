import asyncio
import datetime

import pytest

import settings
from app.openai_helpers.llm_client_factory import LLMClientFactory
from tests.helpers.mock_llm_client import MockLLMClient
from tests.helpers.telegram_factory import make_text_message, make_command_message
from tests.helpers.bot_spy import BotSpy


class TestContextManagement:

    async def test_reset_clears_context(self, bot_app, db_pool):
        """After /reset, next message should not have previous messages in LLM context."""
        telegram_bot, dp, mock_bot = bot_app

        user_id = 66666

        # Send "Message A"
        mock_llm = MockLLMClient()
        mock_llm.add_response("Response A")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update_a = make_text_message('Message A', user_id=user_id)
        await dp.process_update(update_a)
        await asyncio.sleep(0.1)

        # Send /reset
        update_reset = make_command_message('reset', user_id=user_id)
        await dp.process_update(update_reset)
        await asyncio.sleep(0.05)

        # Send "Message B" with fresh LLM
        mock_llm2 = MockLLMClient()
        mock_llm2.add_response("Response B")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm2

        update_b = make_text_message('Message B', user_id=user_id)
        await dp.process_update(update_b)
        await asyncio.sleep(0.1)

        # LLM context for Message B should NOT contain "Message A"
        assert len(mock_llm2.calls) == 1
        messages = mock_llm2.calls[0]['messages']
        all_content = ' '.join(str(m.get('content', '')) for m in messages)
        assert 'Message A' not in all_content, \
            f"Expected 'Message A' not in context after /reset, got: {all_content}"

    async def test_message_expiration_starts_fresh_context(self, bot_app, db_pool):
        """Messages older than MESSAGE_EXPIRATION_WINDOW start fresh context."""
        telegram_bot, dp, mock_bot = bot_app

        user_id = 66667

        # Send "Message A"
        mock_llm = MockLLMClient()
        mock_llm.add_response("Response A")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update_a = make_text_message('Message A', user_id=user_id)
        await dp.process_update(update_a)
        await asyncio.sleep(0.1)

        # Age all messages by 2 hours (MESSAGE_EXPIRATION_WINDOW defaults to 3600s = 1h)
        two_hours_ago = datetime.datetime.now(settings.POSTGRES_TIMEZONE) - datetime.timedelta(hours=2)
        await db_pool.execute(
            "UPDATE chatgpttg.message SET activation_dtime = $1 WHERE tg_chat_id = $2",
            two_hours_ago, user_id,
        )

        # Send "Message B" with fresh LLM
        mock_llm2 = MockLLMClient()
        mock_llm2.add_response("Response B")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm2

        update_b = make_text_message('Message B', user_id=user_id)
        await dp.process_update(update_b)
        await asyncio.sleep(0.1)

        # LLM context should NOT contain "Message A"
        assert len(mock_llm2.calls) == 1
        messages = mock_llm2.calls[0]['messages']
        all_content = ' '.join(str(m.get('content', '')) for m in messages)
        assert 'Message A' not in all_content, \
            f"Expected 'Message A' not in context after expiration, got: {all_content}"

    async def test_reply_to_bot_message_loads_branch(self, bot_app, db_pool):
        """Replying to a bot message loads that branch's context, not linear context."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)

        user_id = 66668

        # Send "Branch A message"
        mock_llm = MockLLMClient()
        mock_llm.add_response("Branch A response")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update_a = make_text_message('Branch A message', user_id=user_id)
        await dp.process_update(update_a)
        await asyncio.sleep(0.1)

        # Get bot response message_id (last sendMessage or editMessageText)
        sent = spy.get_sent_messages()
        # The bot_response_msg_id is what was stored in DB as tg_message_id
        # Our mock returns incrementing IDs; find the one with the response text
        bot_response_texts = [(m.get('text', ''), m.get('message_id')) for m in sent]
        # The actual message_id comes from the mock_request return value
        # We need to find it from the spy - look at the call results
        # Since mock returns dicts, we need the message_id from the return value
        # Let's get it from DB instead
        user = await telegram_bot.db.get_user(user_id)
        last_msg = await telegram_bot.db.get_last_message(user.id, user_id)
        bot_response_tg_msg_id = last_msg.tg_message_id

        # /reset to clear linear context
        update_reset = make_command_message('reset', user_id=user_id)
        await dp.process_update(update_reset)
        await asyncio.sleep(0.05)

        # Send "Branch B message" (new linear context)
        mock_llm2 = MockLLMClient()
        mock_llm2.add_response("Branch B response")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm2

        update_b = make_text_message('Branch B message', user_id=user_id)
        await dp.process_update(update_b)
        await asyncio.sleep(0.1)

        # Reply to Branch A's bot response
        mock_llm3 = MockLLMClient()
        mock_llm3.add_response("Follow up response")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm3

        update_reply = make_text_message(
            'Follow up on A',
            user_id=user_id,
            reply_to_message_id=bot_response_tg_msg_id,
        )
        await dp.process_update(update_reply)
        await asyncio.sleep(0.1)

        # LLM context for the reply should contain "Branch A" but NOT "Branch B"
        assert len(mock_llm3.calls) == 1
        messages = mock_llm3.calls[0]['messages']
        all_content = ' '.join(str(m.get('content', '')) for m in messages)
        assert 'Branch A' in all_content, \
            f"Expected 'Branch A' in reply context, got: {all_content}"
        assert 'Branch B' not in all_content, \
            f"Expected 'Branch B' NOT in reply context, got: {all_content}"
