from aiogram.types import Message, ParseMode

from app.bot.chatgpt_manager import ChatGptManager
from app.bot.utils import send_telegram_message, detect_and_extract_code
from app.context.context_manager import build_context_manager
from app.context.dialog_manager import DialogUtils
from app.openai_helpers.chatgpt import ChatGPT
from app.storage.db import DB, User


class MessageProcessor:
    def __init__(self, db: DB, user: User, message: Message):
        self.db = db
        self.user = user
        self.message = message

    async def add_text_as_context(self, text: str, message_id: int):
        context_manager = await build_context_manager(self.db, self.user, self.message)
        speech_dialog_message = DialogUtils.prepare_user_message(text)
        await context_manager.add_message(speech_dialog_message, message_id)

    async def process_message(self):
        context_manager = await build_context_manager(self.db, self.user, self.message)

        function_storage = await context_manager.get_function_storage()
        chat_gpt_manager = ChatGptManager(ChatGPT(self.user.current_model, self.user.gpt_mode, function_storage), self.db)

        user_dialog_message = DialogUtils.prepare_user_message(self.message.text)
        context_dialog_messages = await context_manager.add_message(user_dialog_message, self.message.message_id)
        response_dialog_message = await chat_gpt_manager.send_user_message(self.user, self.message, context_dialog_messages)

        await self.handle_gpt_response(
            chat_gpt_manager, context_manager, response_dialog_message, function_storage
        )

    async def handle_gpt_response(self, chat_gpt_manager, context_manager, response_dialog_message, function_storage):
        if response_dialog_message.function_call:
            function_name = response_dialog_message.function_call.name
            function_args = response_dialog_message.function_call.arguments
            function_response_raw = await function_storage.run_function(function_name, function_args)

            function_response = DialogUtils.prepare_function_response(function_name, function_response_raw)
            if self.user.function_call_verbose:
                function_response_text = f'Function call: {function_name}({function_args})\n\n{function_response_raw}'
                function_response_tg_message = await send_telegram_message(self.message, function_response_text)
                function_response_message_id = function_response_tg_message.message_id
            else:
                function_response_message_id = -1
            context_dialog_messages = await context_manager.add_message(function_response, function_response_message_id)
            response_dialog_message = await chat_gpt_manager.send_user_message(self.user, self.message, context_dialog_messages)

            await self.handle_gpt_response(chat_gpt_manager, context_manager, response_dialog_message, function_storage)
        else:
            code_fragments = detect_and_extract_code(response_dialog_message.content)
            parse_mode = ParseMode.MARKDOWN if code_fragments else None
            response = await send_telegram_message(self.message, response_dialog_message.content, parse_mode)
            await context_manager.add_message(response_dialog_message, response.message_id)
