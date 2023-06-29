from app import settings
from app.chat.telegramchat import TelegramChat
from app.openai_helpers.chatgpt import ChatGPT
from app.openai_helpers.utils import set_openai_token
from app.storage.db import DBFactory

from aiogram import types, Dispatcher, Bot, executor

bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
dp = Dispatcher(bot)
chat_gpt = ChatGPT()


async def on_startup(dp):
    dp.bot['db'] = await DBFactory().create_database(
        settings.POSTGRES_USER, settings.POSTGRES_PASSWORD,
        settings.POSTGRES_HOST,settings.POSTGRES_PORT, settings.POSTGRES_DATABASE
    )
    dp.bot['chat'] = TelegramChat(dp.bot['db'], chat_gpt)


async def on_shutdown(_):
    await DBFactory().close_database()


@dp.message_handler()
async def handler(message: types.Message) -> None:
    chat: TelegramChat = message.bot['chat']
    await chat.handle_message(message)


if __name__ == '__main__':
    set_openai_token(settings.OPENAI_TOKEN)
    executor.start_polling(dp, on_startup=on_startup, on_shutdown=on_shutdown)
