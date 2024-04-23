from typing import List, Optional

from aiogram import types

import settings
from app.context.dialog_manager import DialogManager
from app.context.function_manager import FunctionManager
from app.llm_models import get_models
from app.openai_helpers.chatgpt import DialogMessage
from app.openai_helpers.function_storage import FunctionStorage
from app.storage.db import DB, User, MessageType


class ContextManager:
    def __init__(self, db: DB, user: User, message: types.Message):
        self.db = db
        self.user = user
        self.message = message
        self.dialog_manager = None
        self.function_manager = None

    async def process_dialog(self):
        models = get_models()
        llm_model = models.get(self.user.current_model)
        if not llm_model:
            raise ValueError(f"Unknown model: {self.user.current_model}")
        context_configuration = llm_model.context_configuration
        self.dialog_manager = DialogManager(self.db, self.user, context_configuration)
        await self.dialog_manager.process_dialog(self.message)

    async def process_functions(self):
        self.function_manager = FunctionManager(self.db, self.user, self.dialog_manager)
        await self.function_manager.process_functions()

    async def process(self):
        await self.process_dialog()
        await self.process_functions()

    async def add_message(self, dialog_message: DialogMessage, tg_message_id: id, message_type: MessageType = MessageType.MESSAGE) -> List[DialogMessage]:
        dialog_messages = await self.dialog_manager.add_message_to_dialog(dialog_message, tg_message_id, message_type)
        return dialog_messages

    async def get_system_prompt(self):
        gpt_mode = settings.gpt_mode.get(self.user.gpt_mode)
        if not gpt_mode:
            raise ValueError(f"Unknown GPT mode: {self.user.gpt_mode}")
        system_prompt = gpt_mode["system"]

        function_storage = await self.get_function_storage()
        if function_storage is not None:
            system_prompt_addition = function_storage.get_system_prompt_addition()
            if system_prompt_addition:
                system_prompt += '\n' + system_prompt_addition

        if self.user.system_prompt_settings:
            system_prompt += f'\n\n<UserSettings>\n{self.user.system_prompt_settings}\n</UserSettings>'

        return system_prompt

    async def get_context_messages(self) -> List[DialogMessage]:
        dialog_messages = self.dialog_manager.get_dialog_messages()
        return dialog_messages

    async def get_function_storage(self) -> Optional[FunctionStorage]:
        return self.function_manager.get_function_storage()


async def build_context_manager(db: DB, user: User, message: types.Message) -> ContextManager:
    context_manager = ContextManager(db, user, message)
    await context_manager.process()
    return context_manager
