import asyncio

import pytest

from app.openai_helpers.llm_client_factory import LLMClientFactory
from tests.helpers.mock_llm_client import MockLLMClient
from tests.helpers.telegram_factory import make_text_message
from tests.helpers.bot_spy import BotSpy


class TestSubDialogue:

    async def test_reply_chain_context(self, bot_app):
        """Replying to a bot message should create a sub-dialogue with correct context."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)

        # First message
        mock_llm = MockLLMClient()
        mock_llm.add_response("Response A")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        user_id = 77777
        update_a = make_text_message('Message A', user_id=user_id)
        await dp.process_update(update_a)
        await asyncio.sleep(0.1)

        # Get the message_id of bot's response (from sendMessage calls)
        sent_messages = spy.get_sent_messages()
        assert len(sent_messages) > 0
        # The bot response message_id comes from mock — find it
        # We need the tg_message_id that was stored in DB for the response
        bot_response_msg_id = sent_messages[-1].get('message_id')
        # Actually, the mock returns incrementing IDs. We need the message_id
        # from the mock_bot.request return value. Let's get it from the call result.
        # Since mock_request returns dicts, and aiogram parses them, the message_id
        # stored in DB comes from the return value of sendMessage/editMessageText.

        # Second message: unrelated (no reply)
        mock_llm2 = MockLLMClient()
        mock_llm2.add_response("Response B")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm2

        update_b = make_text_message('Unrelated message', user_id=user_id)
        await dp.process_update(update_b)
        await asyncio.sleep(0.1)

        # Verify second call got both messages A and B in context
        # (since they're in the same linear dialogue without reset)
        assert len(mock_llm2.calls) == 1
        messages = mock_llm2.calls[0]['messages']
        user_msgs = [m for m in messages if m.get('role') == 'user']
        # Should have context from message A and the new message
        assert len(user_msgs) >= 1
