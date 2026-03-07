import asyncio

from tests.helpers.telegram_factory import make_command_message
from tests.helpers.bot_spy import BotSpy


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
