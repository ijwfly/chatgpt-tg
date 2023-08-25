from aiogram.types import Message

from app.context.context_manager import build_context_manager
from app.context.dialog_manager import DialogUtils
from app.storage.db import DB, User


class MessageProcessor:
    def __init__(self, db: DB, user: User, message: Message):
        self.db = db
        self.user = user
        self.message = message

    async def add_text_as_context(self, text: str, message_id: int):
        context_manager = await build_context_manager(self.db, self.user, self.message)
        speech_dialog_message = DialogUtils.prepare_user_message(text)
        await context_manager.add_message(speech_dialog_message, message_id)
