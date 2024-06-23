import logging

import settings
from app.bot.telegram_bot import TelegramBot
from app.bot.utils import get_image_proxy_url
from app.openai_helpers.utils import OpenAIAsync

from aiogram import Bot, Dispatcher

bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
dp = Dispatcher(bot)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


if __name__ == '__main__':
    # needed for whisper and tts capabilities
    OpenAIAsync.init(settings.OPENAI_TOKEN, settings.OPENAI_BASE_URL)

    # HACK: find if image_proxy is hosted on another machine or not
    image_proxy_url = get_image_proxy_url()
    logger.info(f'Image proxy url: {image_proxy_url}')

    telegram_bot = TelegramBot(bot, dp)
    telegram_bot.run()
