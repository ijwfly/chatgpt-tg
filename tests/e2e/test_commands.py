import asyncio

import pytest

from app.openai_helpers.llm_client_factory import LLMClientFactory
from tests.helpers.mock_llm_client import MockLLMClient
from tests.helpers.telegram_factory import make_text_message, make_command_message
from tests.helpers.bot_spy import BotSpy


@pytest.fixture
def mock_llm():
    client = MockLLMClient()
    return client


class TestCommands:

    async def test_reset_command(self, bot_app):
        """The /reset command should respond with acknowledgment."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)

        update = make_command_message('reset')
        await dp.process_update(update)
        await asyncio.sleep(0.05)

        # /reset responds with emoji acknowledgment
        spy.assert_sent_text_contains('\U0001f44c')

    async def test_usage_command(self, bot_app):
        """The /usage command should respond with usage info."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)

        update = make_command_message('usage')
        await dp.process_update(update)
        await asyncio.sleep(0.05)

        # /usage responds with "Total:" in the message
        spy.assert_sent_text_contains('Total:')

    async def test_usage_shows_stored_price(self, bot_app, mock_llm):
        """The /usage command should show prices stored in the DB."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)

        mock_llm.add_response("Hello!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Hi')
        await dp.process_update(update)
        await asyncio.sleep(0.1)

        # Now send /usage
        spy = BotSpy(mock_bot)
        update = make_command_message('usage')
        await dp.process_update(update)
        await asyncio.sleep(0.05)

        spy.assert_sent_text_contains('$')
        texts = spy.get_all_sent_texts() + spy.get_all_edited_texts()
        assert not any('Something went wrong' in t for t in texts)

    async def test_usage_with_unknown_model_still_works(self, bot_app, mock_llm, db_pool):
        """Usage reporting should work for models removed from code."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)

        # Send a message to create the user
        mock_llm.add_response("Hello!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Hi')
        await dp.process_update(update)
        await asyncio.sleep(0.1)

        # Insert a fake completion_usage row for a removed model
        user_id = await db_pool.fetchval(
            "SELECT id FROM chatgpttg.user WHERE telegram_id = $1", 12345
        )
        await db_pool.execute(
            """INSERT INTO chatgpttg.completion_usage
               (user_id, model, prompt_tokens, completion_tokens, total_tokens, price)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            user_id, 'gpt-removed', 100, 50, 150, 0.5
        )

        # Now send /usage — should not crash and should include the removed model's price
        spy = BotSpy(mock_bot)
        update = make_command_message('usage')
        await dp.process_update(update)
        await asyncio.sleep(0.05)

        spy.assert_sent_text_contains('gpt-removed')
        spy.assert_sent_text_contains('$')
