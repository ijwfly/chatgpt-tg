from typing import List

from app.openai_helpers.chatgpt import DialogMessage
from app.storage.db import DB


class ChatGptManager:
    def __init__(self, chatgpt, db):
        self.chatgpt = chatgpt
        self.db: DB = db

    async def send_user_message(self, user, messages: List[DialogMessage]) -> DialogMessage:
        dialog_message, completion_usage = await self.chatgpt.send_messages(messages)
        await self.db.create_completion_usage(user.id, completion_usage.prompt_tokens, completion_usage.completion_tokens, completion_usage.total_tokens, completion_usage.model)
        return dialog_message
