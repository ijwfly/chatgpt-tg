from typing import Optional

from aiogram.types import Message

from app.bot.utils import send_telegram_message, send_photo


class TelegramSideEffectHandler:
    def __init__(self, message: Message):
        self.message = message

    async def send_message(self, text: str) -> int:
        response = await send_telegram_message(self.message, text)
        return response.message_id

    async def send_photo(self, photo_bytes: bytes, caption: Optional[str] = None) -> int:
        response = await send_photo(self.message, photo_bytes, caption)
        return response.message_id

    async def edit_message(self, message_id: int, text: str) -> None:
        chat_id = self.message.chat.id
        await self.message.bot.edit_message_text(text, chat_id, message_id)
