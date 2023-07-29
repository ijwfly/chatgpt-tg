from typing import List

from app.bot.utils import message_is_forward
from app.openai_helpers.chatgpt import DialogMessage
from app.storage.db import User, DB

from aiogram import types


class DialogManager:
    """
    Default dialog manager which uses Dialog object to manage dialog messages and supports subdialogs
    """
    def __init__(self, db: DB, user: User):
        self.db = db
        self.user = user
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

        if message.reply_to_message is not None and not message_is_forward(message):
            self.is_subdialog = True
            return await self.process_sub_dialog(message)
        else:
            return await self.process_main_dialog(message)

    async def add_message_to_dialog(self, dialog_message: DialogMessage, tg_message_id: id) -> List[DialogMessage]:
        dialog_message = await self.db.create_dialog_message(
            self.dialog_id, self.user.id, self.chat_id, tg_message_id,
            dialog_message, self.dialog_messages, self.is_subdialog
        )
        self.dialog_messages.append(dialog_message)
        return self.get_dialog_messages()

    def get_dialog_messages(self) -> List[DialogMessage]:
        if self.dialog_messages is None:
            raise ValueError('You must call process_dialog first')
        dialog_messages = [d.message for d in self.dialog_messages]
        return dialog_messages


class DynamicDialogManager:
    """
    Dialog manager to manage dialog without Dialog object using dynamic dialog building
    """
    def __init__(self, db: DB, user: User):
        self.db = db
        self.user = user
        self.dialog_messages = None
        self.chat_id = None

        # no subdialog mechanism
        self.is_subdialog = False
        # dialog is not needed, so set up stub value
        self.dialog_id = -1

    async def process_dialog(self, message: types.Message) -> List[DialogMessage]:
        self.chat_id = message.chat.id

        last_message = await self.db.get_last_message(self.user.id, self.chat_id)
        if not last_message:
            self.dialog_messages = []
            return []

        dialog_messages = await self.db.get_messages_by_ids(last_message.previous_message_ids)
        self.dialog_messages = [last_message] + dialog_messages
        return self.get_dialog_messages()

    async def add_message_to_dialog(self, dialog_message: DialogMessage, tg_message_id: id) -> List[DialogMessage]:
        dialog_message = await self.db.create_dialog_message(
            self.dialog_id, self.user.id, self.chat_id, tg_message_id,
            dialog_message, self.dialog_messages, self.is_subdialog
        )
        self.dialog_messages.append(dialog_message)
        return self.get_dialog_messages()

    def get_dialog_messages(self) -> List[DialogMessage]:
        if self.dialog_messages is None:
            raise ValueError('You must call process_dialog first')
        dialog_messages = [d.message for d in self.dialog_messages]
        return dialog_messages


class DialogUtils:
    @staticmethod
    def prepare_user_message(message_text: str) -> DialogMessage:
        return DialogMessage(role="user", content=message_text)

    @staticmethod
    def prepare_function_response(function_name, function_response):
        return DialogMessage(role="function", name=function_name, content=function_response)
