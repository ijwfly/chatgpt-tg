import dataclasses
from typing import List

from aiogram import types

from app.bot.dialog_manager import DialogManager
from app.openai_helpers.chatgpt import DialogMessage
from app.storage.db import DB, User


@dataclasses.dataclass
class ContextConfiguration:
    model_name: str

    # long term memory is based on embedding context search
    long_term_memory_tokens: int
    # mid term memory is used for storing summaries of short term memory
    mid_term_memory_tokens: int
    # short term memory is used for storing last messages
    short_term_memory_tokens: int

    @staticmethod
    def get_config(model: str):
        if model == 'gpt-3.5-turbo':
            return ContextConfiguration(
                model_name=model,
                long_term_memory_tokens=512,
                mid_term_memory_tokens=512,
                short_term_memory_tokens=2560,
            )
        elif model == 'gpt-3.5-turbo-16k':
            return ContextConfiguration(
                model_name=model,
                long_term_memory_tokens=1024,
                mid_term_memory_tokens=1024,
                short_term_memory_tokens=4096,
            )
        elif model == 'gpt-4':
            return ContextConfiguration(
                model_name=model,
                long_term_memory_tokens=512,
                mid_term_memory_tokens=1024,
                short_term_memory_tokens=2048,
            )
        else:
            raise ValueError(f'Unknown model name: {model}')


class ContextManager:
    def __init__(self, db: DB, user: User, message: types.Message):
        self.db = db
        self.user = user
        self.message = message
        self.dialog_manager = None

    async def process_dialog(self):
        context_configuration = ContextConfiguration.get_config(self.user.current_model)
        dialog_manager = DialogManager(self.db, self.user, context_configuration)
        await dialog_manager.process_dialog(self.message)
        self.dialog_manager = dialog_manager

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
