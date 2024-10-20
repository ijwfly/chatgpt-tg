import datetime
from typing import List, Optional, Union

import settings
from app.bot.utils import message_is_forward
from app.openai_helpers.chatgpt import DialogMessage, summarize_messages, DialogMessageContentPart
from app.openai_helpers.count_tokens import count_dialog_messages_tokens
from app.openai_helpers.utils import calculate_completion_usage_price
from app.storage.db import User, DB, Message, MessageType

from aiogram import types


class DialogManager:
    def __init__(self, db: DB, user: User, context_configuration):
        self.db = db
        self.user = user
        self.messages: Optional[List[Message]] = None
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
            if db_message is not None and db_message.activation_dtime < message_expiration_dtime:
                # last message is too old, starting new dialog
                db_message = None

        if not db_message or db_message.message_type == MessageType.RESET:
            self.messages = []
            return []

        dialog_messages = await self.db.get_messages_by_ids(db_message.previous_message_ids)
        dialog_messages.append(db_message)

        if is_reply:
            # if it's a reply, we need to update activation time of dialog messages to be included in context next time
            await self.db.update_activation_dtime([m.id for m in dialog_messages])

        self.messages = await self.summarize_messages_if_needed(dialog_messages)
        return self.get_dialog_messages()

    def split_context_by_token_length(self, messages: List[Message]):
        token_length = self.context_configuration.short_term_memory_tokens / 2
        for split_point in range(len(messages)):
            right_dialog_messages = (d.message for d in messages[split_point:])
            right_length = count_dialog_messages_tokens(right_dialog_messages, self.user.current_model)
            if right_length <= token_length:
                return messages[:split_point], messages[split_point:]
        else:
            return messages, []

    async def summarize_messages_if_needed(self, messages: List[Message]):
        message_tokens_count = count_dialog_messages_tokens((m.message for m in messages), self.user.current_model)
        if message_tokens_count > self.context_configuration.hard_max_context_size:
            # this is safety measure, we should never get here
            # if hard limit is exceeded, the context is too big to summarize or to process
            raise ValueError(f'Hard context size is exceeded: {message_tokens_count}')

        if self.user.auto_summarize and message_tokens_count >= self.context_configuration.short_term_memory_tokens:
            to_summarize, to_process = self.split_context_by_token_length(messages)
            summarized_message = await self.summarize_messages(to_summarize)
            return [summarized_message] + to_process
        else:
            return messages

    async def summarize_messages(self, messages: List[Message]):
        summarized, completion_usage = await summarize_messages(
            [m.message for m in messages], self.user.current_model, self.context_configuration.summary_length
        )
        price = calculate_completion_usage_price(completion_usage.prompt_tokens, completion_usage.completion_tokens, completion_usage.model)
        await self.db.create_completion_usage(
            self.user.id, completion_usage.prompt_tokens, completion_usage.completion_tokens,
            completion_usage.total_tokens, completion_usage.model, price
        )

        summarized_message = DialogUtils.prepare_user_message(f"Summarized previous conversation:\n{summarized}")
        tg_message_id = -1
        message = await self.db.create_message(
            self.user.id, self.chat_id, tg_message_id, summarized_message, [], MessageType.SUMMARY
        )
        return message

    async def add_message_to_dialog(self, message: DialogMessage, tg_message_id: id,
                                    message_type: MessageType = MessageType.MESSAGE) -> List[DialogMessage]:
        message = await self.db.create_message(
            self.user.id, self.chat_id, tg_message_id, message, self.messages, message_type
        )
        self.messages.append(message)
        self.messages = await self.summarize_messages_if_needed(self.messages)
        return self.get_dialog_messages()

    def get_dialog_messages(self) -> List[DialogMessage]:
        if self.messages is None:
            raise ValueError('You must call process_dialog first')
        dialog_messages = [d.message for d in self.messages]
        return dialog_messages


class DialogUtils:
    CONTENT_TEXT = 'text'
    CONTENT_IMAGE_URL = 'image_url'

    @staticmethod
    def prepare_user_message(content: Union[str, List[DialogMessageContentPart]]) -> DialogMessage:
        return DialogMessage(role="user", content=content)

    @staticmethod
    def construct_image_url(image_url: str):
        return {"url": image_url}

    @classmethod
    def construct_message_content_part(cls, content_type: str, content: str):
        if content_type == cls.CONTENT_IMAGE_URL:
            content = cls.construct_image_url(content)

        return {"type": content_type, content_type: content}

    @staticmethod
    def prepare_function_response(function_name, function_response):
        return DialogMessage(role="function", name=function_name, content=function_response)

    @staticmethod
    def prepare_tool_call_response(tool_call_id, tool_call_response):
        return DialogMessage(role="tool", tool_call_id=tool_call_id, content=tool_call_response)
