import asyncio

import pytest

from app.openai_helpers.llm_client_factory import LLMClientFactory
from tests.helpers.mock_llm_client import MockLLMClient
from tests.helpers.telegram_factory import make_text_message
from tests.helpers.bot_spy import BotSpy


@pytest.fixture
def mock_llm():
    client = MockLLMClient()
    return client


class TestSimpleMessage:

    async def test_text_message_gets_response(self, bot_app, mock_llm):
        """Send a text message, verify bot responds with LLM output."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)

        mock_llm.add_response("Hello! I'm a test bot.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Hi there')
        await dp.process_update(update)
        await asyncio.sleep(0.1)

        spy.assert_sent_text_contains("Hello! I'm a test bot.")

    async def test_user_created_in_db(self, bot_app, mock_llm):
        """After sending a message, user should exist in DB."""
        telegram_bot, dp, mock_bot = bot_app

        mock_llm.add_response("Response text")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Hello', user_id=99999)
        await dp.process_update(update)
        await asyncio.sleep(0.1)

        user = await telegram_bot.db.get_user(99999)
        assert user is not None
        assert user.telegram_id == 99999

    async def test_message_saved_in_db(self, bot_app, mock_llm):
        """User message and bot response should be saved in DB."""
        telegram_bot, dp, mock_bot = bot_app

        mock_llm.add_response("Saved response")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        user_id = 88888
        update = make_text_message('Test message', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.1)

        user = await telegram_bot.db.get_user(user_id)
        assert user is not None
        last_msg = await telegram_bot.db.get_last_message(user.id, user_id)
        assert last_msg is not None

    async def test_llm_receives_user_message(self, bot_app, mock_llm):
        """Verify the LLM client receives the user's message in context."""
        telegram_bot, dp, mock_bot = bot_app

        mock_llm.add_response("I got your message")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('What is 2+2?')
        await dp.process_update(update)
        await asyncio.sleep(0.1)

        assert len(mock_llm.calls) == 1
        messages = mock_llm.calls[0]['messages']
        # First message is system prompt, last should contain user text
        user_messages = [m for m in messages if m.get('role') == 'user']
        assert any('What is 2+2?' in str(m.get('content', '')) for m in user_messages)
