from typing import Optional

from aiogram import Bot

from app.bot.rich_messages import send_rich_message_to_chat
from app.bot.utils import download_telegram_file


class BotSideEffectHandler:
    """SideEffectHandler that works via Bot + chat_id (no aiogram Message needed).

    Used by SchedulerService for scheduled task execution where there is
    no incoming Telegram message to reply to.
    """

    def __init__(self, bot: Bot, chat_id: int):
        self.bot = bot
        self.chat_id = chat_id

    async def send_message(self, text: str) -> int:
        result = await self.bot.send_message(chat_id=self.chat_id, text=text)
        return result.message_id

    async def send_rich_message(self, markdown: str) -> int:
        """LLM answers are rich markdown; service texts go through send_message."""
        result = await send_rich_message_to_chat(self.bot, self.chat_id, markdown)
        return result.message_id

    async def edit_message(self, message_id: int, text: str) -> None:
        await self.bot.edit_message_text(text=text, chat_id=self.chat_id, message_id=message_id)

    async def download_file(self, file_id: str, max_bytes: Optional[int] = None) -> bytes:
        return await download_telegram_file(self.bot, file_id, max_bytes)
