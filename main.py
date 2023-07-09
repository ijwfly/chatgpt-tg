import settings
from app.bot.queued_dispatcher import QueuedDispatcher
from app.bot.telegram_bot import TelegramBot
from app.functions.wolframalpha import query_wolframalpha
from app.openai_helpers.function_storage import FunctionStorage
from app.openai_helpers.utils import set_openai_token

from aiogram import Bot

bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
dp = QueuedDispatcher(bot)


def setup_function_storage() -> FunctionStorage:
    functions = []

    if settings.ENABLE_WOLFRAMALPHA:
        functions.append(query_wolframalpha)

    function_storage = FunctionStorage()
    for function in functions:
        function_storage.register(function)
    return function_storage


if __name__ == '__main__':
    set_openai_token(settings.OPENAI_TOKEN)
    function_storage = setup_function_storage()
    telegram_bot = TelegramBot(bot, dp, function_storage)
    telegram_bot.run()
