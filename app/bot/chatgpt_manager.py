from typing import List

from app.openai_helpers.chatgpt import DialogMessage
from app.storage.db import DB, User


class ChatGptManager:
    def __init__(self, chatgpt, db):
        self.chatgpt = chatgpt
        self.db: DB = db

    async def send_user_message(self, user: User, messages: List[DialogMessage]) -> DialogMessage:
        if user.streaming_answers:
            return self.send_user_message_streaming(user, messages)
        else:
            return self.send_user_message_sync(user, messages)

    async def send_user_message_sync(self, user: User, messages: List[DialogMessage]) -> DialogMessage:
        dialog_message, completion_usage = await self.chatgpt.send_messages(messages)
        await self.db.create_completion_usage(user.id, completion_usage.prompt_tokens, completion_usage.completion_tokens, completion_usage.total_tokens, completion_usage.model)
        yield dialog_message

    async def send_user_message_streaming(self, user: User, messages: List[DialogMessage]) -> DialogMessage:
        dialog_message = None
        completion_usage = None
        async for dialog_message, completion_usage in self.chatgpt.send_messages_streaming(messages):
            yield dialog_message

        if dialog_message is None or completion_usage is None:
            raise ValueError("Call to ChatGPT failed")

        await self.db.create_completion_usage(user.id, completion_usage.prompt_tokens, completion_usage.completion_tokens, completion_usage.total_tokens, completion_usage.model)
        yield dialog_message
