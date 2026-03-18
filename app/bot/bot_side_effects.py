from typing import Optional

from aiogram import Bot


class BotSideEffectHandler:
    """SideEffectHandler that works via Bot + chat_id (no aiogram Message needed).

    Used by SchedulerService for scheduled task execution where there is
    no incoming Telegram message to reply to.
    """

    def __init__(self, bot: Bot, chat_id: int):
        self.bot = bot
        self.chat_id = chat_id

    async def send_message(self, text: str) -> int:
        result = await self.bot.send_message(self.chat_id, text)
        return result.message_id

    async def send_photo(self, photo_bytes: bytes, caption: Optional[str] = None) -> int:
        result = await self.bot.send_photo(self.chat_id, photo_bytes, caption=caption)
        return result.message_id

    async def edit_message(self, message_id: int, text: str) -> None:
        await self.bot.edit_message_text(text, self.chat_id, message_id)
