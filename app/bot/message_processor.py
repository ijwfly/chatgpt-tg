from datetime import datetime

import settings
from app.bot.cancellation_manager import get_cancel_button
from app.bot.chatgpt_manager import ChatGptManager
from app.bot.utils import send_telegram_message, detect_and_extract_code, edit_telegram_message
from app.context.context_manager import build_context_manager
from app.context.dialog_manager import DialogUtils
from app.openai_helpers.chatgpt import ChatGPT
from app.storage.db import DB, User

from aiogram.types import Message, ParseMode, InlineKeyboardMarkup

WAIT_BETWEEN_MESSAGE_UPDATES = 2


class MessageProcessor:
    def __init__(self, db: DB, user: User, message: Message):
        self.db = db
        self.user = user
        self.message = message

    async def add_text_as_context(self, text: str, message_id: int):
        context_manager = await build_context_manager(self.db, self.user, self.message)
        dialog_message = DialogUtils.prepare_user_message(text)
        await context_manager.add_message(dialog_message, message_id)

    async def add_message_as_context(self, message_id: int = None):
        if message_id is None:
            message_id = self.message.message_id
        context_manager = await build_context_manager(self.db, self.user, self.message)
        dialog_message = await self.prepare_user_message(self.message)
        await context_manager.add_message(dialog_message, message_id)

    @staticmethod
    async def prepare_user_message(message: Message):
        content = []
        if message.text:
            content.append(DialogUtils.construct_message_content_part(DialogUtils.CONTENT_TEXT, message.text))

        if message.photo:
            # get largest photo
            file_id = message.photo[-1].file_id
            file_url = f'{settings.IMAGE_PROXY_HOSTNAME}/{file_id}.jpg'
            content.append(DialogUtils.construct_message_content_part(DialogUtils.CONTENT_IMAGE_URL, file_url))

        return DialogUtils.prepare_user_message(content)

    async def process_message(self, is_cancelled):
        context_manager = await build_context_manager(self.db, self.user, self.message)

        function_storage = await context_manager.get_function_storage()
        chat_gpt_manager = ChatGptManager(ChatGPT(self.user.current_model, self.user.gpt_mode, function_storage), self.db)

        user_dialog_message = await self.prepare_user_message(self.message)
        context_dialog_messages = await context_manager.add_message(user_dialog_message, self.message.message_id)
        response_generator = await chat_gpt_manager.send_user_message(self.user, context_dialog_messages, is_cancelled)

        await self.handle_gpt_response(
            chat_gpt_manager, context_manager, response_generator, function_storage, is_cancelled
        )

    async def handle_gpt_response(self, chat_gpt_manager, context_manager, response_generator, function_storage, is_cancelled):
        response_dialog_message, message_id = await self.handle_response_generator(response_generator)
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
            response_generator = await chat_gpt_manager.send_user_message(self.user, context_dialog_messages, is_cancelled)

            await self.handle_gpt_response(chat_gpt_manager, context_manager, response_generator, function_storage, is_cancelled)
        else:
            code_fragments = detect_and_extract_code(response_dialog_message.content)
            parse_mode = ParseMode.MARKDOWN if code_fragments else None
            if message_id is not None:
                response = await edit_telegram_message(self.message, response_dialog_message.content, message_id, parse_mode)
            else:
                response = await send_telegram_message(self.message, response_dialog_message.content, parse_mode)
            await context_manager.add_message(response_dialog_message, response.message_id)

    async def handle_response_generator(self, response_generator):
        dialog_message = None
        message_id = None
        chat_id = None
        previous_content = None
        previous_time = None

        keyboard = InlineKeyboardMarkup()
        keyboard.add(get_cancel_button())

        first_iteration = True
        async for dialog_message in response_generator:
            if first_iteration:
                # HACK: skip first iteration for case with full synchronous openai response
                first_iteration = False
                continue

            if dialog_message.function_call is not None:
                continue

            new_content = ' '.join(dialog_message.content.strip().split(' ')[:-1]) if dialog_message.content else ''
            if len(new_content) < 50:
                continue

            # send message
            if not message_id:
                resp = await send_telegram_message(self.message, dialog_message.content, reply_markup=keyboard)
                chat_id = self.message.chat.id
                # hack: most telegram clients remove "typing" status after receiving new message from bot
                await self.message.bot.send_chat_action(chat_id, 'typing')
                message_id = resp.message_id
                previous_content = dialog_message.content
                previous_time = datetime.now()
                continue

            # update message
            time_passed_seconds = (datetime.now() - previous_time).seconds
            if previous_content != new_content and time_passed_seconds >= WAIT_BETWEEN_MESSAGE_UPDATES:
                await self.message.bot.edit_message_text(new_content, chat_id, message_id, reply_markup=keyboard)
                previous_content = new_content
                previous_time = datetime.now()
        return dialog_message, message_id
