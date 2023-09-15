from datetime import datetime
from typing import List

from app.openai_helpers.chatgpt import DialogMessage
from app.storage.db import DB, User

from aiogram.types import Message


class ChatGptManager:
    def __init__(self, chatgpt, db):
        self.chatgpt = chatgpt
        self.db: DB = db

    async def send_user_message(self, user: User, tg_message: Message, messages: List[DialogMessage]) -> DialogMessage:
        if user.streaming_answers:
            return await self.send_user_message_streaming(user, tg_message, messages)
        else:
            return await self.send_user_message_sync(user, messages)

    async def send_user_message_sync(self, user: User, messages: List[DialogMessage]) -> DialogMessage:
        dialog_message, completion_usage = await self.chatgpt.send_messages(messages)
        await self.db.create_completion_usage(user.id, completion_usage.prompt_tokens, completion_usage.completion_tokens, completion_usage.total_tokens, completion_usage.model)
        return dialog_message

    async def send_user_message_streaming(self, user: User, tg_message: Message, messages: List[DialogMessage]) -> DialogMessage:
        dialog_message = None
        completion_usage = None
        message_id = None
        chat_id = None
        previous_content = None
        previous_time = None
        async for dialog_message, completion_usage in self.chatgpt.send_messages_streaming(messages):
            if dialog_message.function_call is not None:
                continue

            new_content = ' '.join(dialog_message.content.strip().split(' ')[:-1]) if dialog_message.content else ''
            if len(new_content) < 50:
                continue

            # send message
            if not message_id:
                resp = await tg_message.answer(dialog_message.content)
                chat_id = tg_message.chat.id
                # hack: most telegram clients remove "typing" status after receiving new message from bot
                await tg_message.bot.send_chat_action(chat_id, 'typing')
                message_id = resp.message_id
                previous_content = dialog_message.content
                previous_time = datetime.now()
                continue

            # update message
            time_passed_seconds = (datetime.now() - previous_time).seconds
            if previous_content != new_content and time_passed_seconds >= 1:
                await tg_message.bot.edit_message_text(new_content, chat_id, message_id)
                previous_content = new_content
                previous_time = datetime.now()

        if message_id:
            await tg_message.bot.delete_message(chat_id, message_id)

        if dialog_message is None or completion_usage is None:
            raise ValueError("Call to ChatGPT failed")

        await self.db.create_completion_usage(user.id, completion_usage.prompt_tokens, completion_usage.completion_tokens, completion_usage.total_tokens, completion_usage.model)
        return dialog_message
