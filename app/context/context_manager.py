from typing import List

from aiogram import types

from app.bot.dialog_manager import DialogManager
from app.openai_helpers.chatgpt import DialogMessage
from app.storage.db import DB, User


class ContextManager:
    def __init__(self, db: DB, user: User, message: types.Message):
        self.db = db
        self.user = user
        self.message = message
        self.dialog_manager = None

    async def process_dialog(self):
        dialog_manager = DialogManager(self.db, self.user)
        await dialog_manager.process_dialog(self.message)
        self.dialog_manager = dialog_manager
        return dialog_manager

    async def process(self):
        await self.process_dialog()

    async def add_message(self, dialog_message: DialogMessage, tg_message_id: id) -> List[DialogMessage]:
        dialog_messages = await self.dialog_manager.add_message_to_dialog(dialog_message, tg_message_id)
        return dialog_messages

    async def get_context_messages(self) -> List[DialogMessage]:
        dialog_messages = self.dialog_manager.get_dialog_messages()
        return dialog_messages


async def build_context_manager(db: DB, user: User, message: types.Message) -> ContextManager:
    context_manager = ContextManager(db, user, message)
    await context_manager.process()
    return context_manager
