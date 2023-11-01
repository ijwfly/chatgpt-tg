import datetime
from typing import List, Optional

import settings
from app.bot.utils import message_is_forward
from app.openai_helpers.chatgpt import DialogMessage, summarize_messages
from app.openai_helpers.count_tokens import count_dialog_messages_tokens
from app.storage.db import User, DB, Message, MessageType

from aiogram import types


class DialogManager:
    def __init__(self, db: DB, user: User, context_configuration):
        self.db = db
        self.user = user
        self.dialog_messages: Optional[List[Message]] = None
        self.chat_id = None
        self.context_configuration = context_configuration

    async def process_dialog(self, message: types.Message) -> List[DialogMessage]:
        self.chat_id = message.chat.id

        if message.reply_to_message is not None and not message_is_forward(message):
            is_reply = True
            db_message = await self.db.get_telegram_message(self.chat_id, message.reply_to_message.message_id)
        else:
            is_reply = False
            db_message = await self.db.get_last_message(self.user.id, self.chat_id)
            message_expiration_dtime = datetime.datetime.now(settings.POSTGRES_TIMEZONE) - datetime.timedelta(seconds=settings.MESSAGE_EXPIRATION_WINDOW)
            if db_message.activation_dtime < message_expiration_dtime:
                # last message is too old, starting new dialog
                db_message = None

        if not db_message or db_message.message_type == MessageType.RESET:
            self.dialog_messages = []
            return []

        dialog_messages = await self.db.get_messages_by_ids(db_message.previous_message_ids)
        dialog_messages.append(db_message)

        if is_reply:
            # if it's a reply, we need to update activation time of dialog messages to be included in context next time
            await self.db.update_activation_dtime([m.id for m in dialog_messages])

        if self.user.auto_summarize and count_dialog_messages_tokens(m.message for m in dialog_messages) >= self.context_configuration.short_term_memory_tokens:
            to_summarize, to_process = self.split_context_by_token_length(dialog_messages)
            summarized_message = await self.summarize_messages(to_summarize)
            self.dialog_messages = [summarized_message] + to_process
        else:
            self.dialog_messages = dialog_messages
        return self.get_dialog_messages()

    def split_context_by_token_length(self, messages: List[Message]):
        token_length = self.context_configuration.short_term_memory_tokens / 2
        for split_point in range(len(messages)):
            right_dialog_messages = (d.message for d in messages[split_point:])
            right_length = count_dialog_messages_tokens(right_dialog_messages)
            if right_length <= token_length:
                return messages[:split_point], messages[split_point:]
        else:
            return messages, []

    async def summarize_messages(self, messages: List[Message]):
        summarized, completion_usage = await summarize_messages(
            [m.message for m in messages], self.user.current_model, self.context_configuration.summary_length
        )
        await self.db.create_completion_usage(
            self.user.id, completion_usage.prompt_tokens, completion_usage.completion_tokens,
            completion_usage.total_tokens, completion_usage.model
        )

        summarized_message = DialogUtils.prepare_user_message(f"Summarized previous conversation:\n{summarized}")
        tg_message_id = -1
        message = await self.db.create_message(
            self.user.id, self.chat_id, tg_message_id, summarized_message, [], MessageType.SUMMARY
        )
        return message

    async def add_message_to_dialog(self, dialog_message: DialogMessage, tg_message_id: id) -> List[DialogMessage]:
        dialog_message = await self.db.create_message(
            self.user.id, self.chat_id, tg_message_id, dialog_message, self.dialog_messages
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
