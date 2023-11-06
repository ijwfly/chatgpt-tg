import logging

import settings
from app.bot.queued_dispatcher import QueuedDispatcher
from app.bot.telegram_bot import TelegramBot
from app.openai_helpers.utils import OpenAIAsync

from aiogram import Bot

bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
dp = QueuedDispatcher(bot)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


if __name__ == '__main__':
    OpenAIAsync.init(settings.OPENAI_TOKEN)
    telegram_bot = TelegramBot(bot, dp)
    telegram_bot.run()
