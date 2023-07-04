from app import settings
from app.bot.telegram_bot import TelegramBot
from app.openai_helpers.utils import set_openai_token

from aiogram import Dispatcher, Bot

bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
dp = Dispatcher(bot)


if __name__ == '__main__':
    set_openai_token(settings.OPENAI_TOKEN)
    telegram_bot = TelegramBot(bot, dp)
    telegram_bot.run()
