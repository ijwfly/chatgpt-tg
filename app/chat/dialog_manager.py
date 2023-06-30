from typing import List

from app.openai_helpers.chatgpt import DialogMessage

from aiogram import types


class DialogManager:
    def __init__(self, db):
        self.db = db
        self.user = None
        self.dialog_id = None
        self.dialog_messages = None
        self.is_subdialog = False
        self.chat_id = None

    async def process_main_dialog(self, message: types.Message) -> List[DialogMessage]:
        dialog = await self.db.get_active_dialog(self.user.id)
        if dialog is None:
            dialog = await self.db.create_active_dialog(self.user.id, message.chat.id)
        self.dialog_id = dialog.id

        self.dialog_messages = await self.db.get_dialog_messages(self.dialog_id)
        dialog_messages = [d.message for d in self.dialog_messages]
        return dialog_messages

    async def process_sub_dialog(self, message: types.Message) -> List[DialogMessage]:
        reply_message_id = message.reply_to_message.message_id
        self.dialog_messages = await self.db.get_subdialog_messages(self.chat_id, reply_message_id)
        if self.dialog_messages:
            self.dialog_id = self.dialog_messages[0].dialog_id
        dialog_messages = [d.message for d in self.dialog_messages]
        return dialog_messages

    async def process_dialog(self, message: types.Message) -> List[DialogMessage]:
        self.chat_id = message.chat.id
        self.user = await self.db.get_user(message.from_user.id)
        if self.user is None:
            self.user = await self.db.create_user(message.from_user.id)

        if message.reply_to_message is not None:
            self.is_subdialog = True
            return await self.process_sub_dialog(message)
        else:
            return await self.process_main_dialog(message)

    async def prepare_input_message(self, message: types.Message):
        request_text = message.text
        return DialogMessage(role="user", content=request_text)

    async def add_message_to_dialog(self, dialog_message: DialogMessage, tg_message_id: id):
        dialog_message = await self.db.create_dialog_message(
            self.dialog_id, self.user.id, self.chat_id, tg_message_id,
            dialog_message, self.dialog_messages, self.is_subdialog
        )
        self.dialog_messages.append(dialog_message)
