import logging

import settings
from app.bot.telegram_bot import TelegramBot
from app.openai_helpers.utils import OpenAIAsync

from aiogram import Bot, Dispatcher

bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
dp = Dispatcher(bot)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


if __name__ == '__main__':
    # needed for whisper and tts capabilities
    OpenAIAsync.init(settings.OPENAI_TOKEN, settings.OPENAI_BASE_URL)
    telegram_bot = TelegramBot(bot, dp)
    telegram_bot.run()
