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


def _init_langfuse():
    """Initialize Langfuse observability if configured."""
    if not settings.LANGFUSE_ENABLED:
        return

    import os
    os.environ.setdefault('LANGFUSE_PUBLIC_KEY', settings.LANGFUSE_PUBLIC_KEY)
    os.environ.setdefault('LANGFUSE_SECRET_KEY', settings.LANGFUSE_SECRET_KEY)
    os.environ.setdefault('LANGFUSE_HOST', settings.LANGFUSE_BASE_URL)

    try:
        from opentelemetry.instrumentation.anthropic import AnthropicInstrumentor
        AnthropicInstrumentor().instrument()
        logger.info('Langfuse: Anthropic instrumentation enabled')
    except ImportError:
        logger.warning('Langfuse: opentelemetry-instrumentation-anthropic not installed, Anthropic calls won\'t be traced')

    logger.info(f'Langfuse observability enabled (base_url={settings.LANGFUSE_BASE_URL})')


if __name__ == '__main__':
    # needed for whisper and tts capabilities
    OpenAIAsync.init(settings.OPENAI_TOKEN, settings.OPENAI_BASE_URL)

    _init_langfuse()

    # HACK: find if image_proxy is hosted on another machine or not
    image_proxy_url = get_image_proxy_url()
    logger.info(f'Image proxy url: {image_proxy_url}')

    telegram_bot = TelegramBot(bot, dp)
    telegram_bot.run()
