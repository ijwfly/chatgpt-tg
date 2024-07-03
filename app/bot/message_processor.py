from contextlib import suppress
from datetime import datetime
from urllib.parse import urljoin

from aiogram.utils.exceptions import BadRequest

import settings
from app.bot.cancellation_manager import get_cancel_button
from app.bot.chatgpt_manager import ChatGptManager
from app.bot.utils import send_telegram_message, edit_telegram_message
from app.context.context_manager import build_context_manager
from app.context.dialog_manager import DialogUtils
from app.llm_models import get_model_by_name
from app.openai_helpers.anthropic_chatgpt import AnthropicChatGPT
from app.openai_helpers.chatgpt import ChatGPT
from app.openai_helpers.count_tokens import calculate_image_tokens
from app.storage.db import DB, User, MessageType

from aiogram.types import Message, ParseMode, InlineKeyboardMarkup

WAIT_BETWEEN_MESSAGE_UPDATES = 2
TELEGRAM_MESSAGE_LENGTH_CUTOFF = 4080


class MessageProcessor:
    def __init__(self, db: DB, user: User, message: Message):
        self.db = db
        self.user = user
        self.message = message
        self._context_manager = None

    async def context_manager(self):
        if self._context_manager is None:
            self._context_manager = await build_context_manager(self.db, self.user, self.message)
        return self._context_manager

    async def add_text_as_context(self, text: str, message_id: int, message_type: MessageType = MessageType.MESSAGE):
        context_manager = await self.context_manager()
        dialog_message = DialogUtils.prepare_user_message(text)
        await context_manager.add_message(dialog_message, message_id, message_type)

    async def add_message_as_context(self, message_id: int = None, message: Message = None):
        if message is None:
            message = self.message
        if message_id is None:
            message_id = message.message_id
        context_manager = await self.context_manager()
        dialog_message = await self.prepare_user_message(message)
        await context_manager.add_message(dialog_message, message_id)

    @staticmethod
    async def prepare_user_message(message: Message):
        if message.photo:
            content = []

            if message.text:
                content.append(DialogUtils.construct_message_content_part(DialogUtils.CONTENT_TEXT, message.text))

            # largest photo
            photo = message.photo[-1]
            file_id = photo.file_id
            # WILD HACK: add tokens count to the url to use it later for context size calculation
            # it's the only place in code where we know image size
            # maybe we should add it to DialogMessage as metadata?
            tokens = calculate_image_tokens(photo.width, photo.height)
            file_url = urljoin(f'{settings.IMAGE_PROXY_URL}:{settings.IMAGE_PROXY_PORT}', f'{file_id}_{tokens}.jpg')
            content.append(DialogUtils.construct_message_content_part(DialogUtils.CONTENT_IMAGE_URL, file_url))

            return DialogUtils.prepare_user_message(content)
        elif message.text:
            return DialogUtils.prepare_user_message(message.text)
        else:
            ValueError("prepare_user_message called with empty message")

    async def process(self, is_cancelled):
        context_manager = await self.context_manager()

        llm_model = get_model_by_name(self.user.current_model)
        function_storage = None
        if llm_model.capabilities.tool_calling or llm_model.capabilities.function_calling:
            function_storage = await context_manager.get_function_storage()
        system_prompt = await context_manager.get_system_prompt()

        # HACK: TODO: refactor to factory
        if self.user.current_model == llm_model.ANTHROPIC_CLAUDE_35_SONNET:
            chat_gpt_manager = ChatGptManager(AnthropicChatGPT(llm_model, system_prompt, function_storage), self.db)
        else:
            chat_gpt_manager = ChatGptManager(ChatGPT(llm_model, system_prompt, function_storage), self.db)

        context_dialog_messages = await context_manager.get_context_messages()
        response_generator = await chat_gpt_manager.send_user_message(self.user, context_dialog_messages, is_cancelled)

        await self.handle_gpt_response(
            chat_gpt_manager, context_manager, response_generator, function_storage, is_cancelled
        )

    async def handle_gpt_response(self, chat_gpt_manager, context_manager, response_generator, function_storage, is_cancelled, recursive_count=0):
        if recursive_count >= settings.SUCCESSIVE_FUNCTION_CALLS_LIMIT:
            # sometimes model starts to make function call retries indefinitely, this is safety measure
            raise ValueError('Model makes too many successive function calls')

        response_dialog_message, message_id = await self.handle_response_generator(response_generator)

        if response_dialog_message.content:
            dialog_messages = self.split_dialog_message(response_dialog_message)
            for dialog_message in dialog_messages:
                # code_fragments = detect_and_extract_code(dialog_message.content)
                # parse_mode = ParseMode.MARKDOWN if code_fragments else None
                parse_mode = ParseMode.MARKDOWN
                if message_id is not None:
                    response = await edit_telegram_message(self.message, dialog_message.content, message_id, parse_mode)
                    message_id = None
                else:
                    response = await send_telegram_message(self.message, dialog_message.content, parse_mode)
                await context_manager.add_message(dialog_message, response.message_id)

        if response_dialog_message.function_call:
            if not response_dialog_message.content:
                # if there is a content in response, context was already updated in the block above
                await context_manager.add_message(response_dialog_message, -1)

            function_call = response_dialog_message.function_call
            function_response_raw, function_response_message_id = await self.run_function_call(response_dialog_message.function_call, function_storage, context_manager)

            if function_response_raw is None:
                # None means there is no need to pass response to GPT or add it to context
                # TODO: add exception to cancel further processing instead of None
                return

            function_response = DialogUtils.prepare_function_response(function_call.name, function_response_raw)
            context_dialog_messages = await context_manager.add_message(function_response, function_response_message_id)
            response_generator = await chat_gpt_manager.send_user_message(self.user, context_dialog_messages, is_cancelled)

            await self.handle_gpt_response(chat_gpt_manager, context_manager, response_generator, function_storage, is_cancelled, recursive_count + 1)

        pass_tool_response_to_gpt = False
        if response_dialog_message.tool_calls:
            context_dialog_messages = None
            if not response_dialog_message.content:
                # if there is a content in response, context was already updated in the block above
                await context_manager.add_message(response_dialog_message, -1)

            for tool_call in response_dialog_message.tool_calls:
                if tool_call.type != 'function':
                    raise ValueError(f'Unknown tool call type: {type}')
                tool_call_id = tool_call.id
                function_call = tool_call.function
                function_response_raw, function_response_message_id = await self.run_function_call(function_call, function_storage, context_manager, tool_call_id)

                if function_response_raw is not None:
                    # None means there is no need to pass response to GPT or add it to context, the response is already added to context from function call
                    # TODO: add exception to cancel further processing instead of None
                    pass_tool_response_to_gpt = True
                    tool_response = DialogUtils.prepare_tool_call_response(tool_call_id, function_response_raw)
                    context_dialog_messages = await context_manager.add_message(tool_response, function_response_message_id)

            if pass_tool_response_to_gpt and context_dialog_messages:
                response_generator = await chat_gpt_manager.send_user_message(self.user, context_dialog_messages, is_cancelled)
                await self.handle_gpt_response(chat_gpt_manager, context_manager, response_generator, function_storage, is_cancelled, recursive_count + 1)

    async def run_function_call(self, function_call, function_storage, context_manager, tool_call_id: str = None):
        function_name = function_call.name
        function_args = function_call.arguments
        function_class = function_storage.get_function_class(function_name)
        function = function_class(self.user, self.db, context_manager, self.message, tool_call_id)
        function_response_raw = await function.run_str_args(function_args)

        function_response_message_id = -1
        if self.user.function_call_verbose:
            with suppress(BadRequest):
                # TODO: split function call message if it's too long
                function_response_text = f'Function call: {function_name}({function_args})\n\nResponse: {function_response_raw}'
                function_response_text = function_response_text[:TELEGRAM_MESSAGE_LENGTH_CUTOFF]
                function_response_tg_message = await send_telegram_message(self.message, function_response_text)
                function_response_message_id = function_response_tg_message.message_id

        return function_response_raw, function_response_message_id

    async def handle_response_generator(self, response_generator):
        dialog_message = None
        message_id = None
        chat_id = None
        previous_content = None
        previous_time = None

        keyboard = InlineKeyboardMarkup()
        keyboard.add(get_cancel_button())

        message_too_long_for_telegram = False
        first_iteration = True
        async for dialog_message in response_generator:
            if first_iteration:
                # HACK: skip first iteration for case with full synchronous openai response
                first_iteration = False
                continue

            if message_too_long_for_telegram:
                continue

            if dialog_message.function_call is not None or dialog_message.tool_calls is not None:
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
                if len(new_content) > TELEGRAM_MESSAGE_LENGTH_CUTOFF:
                    # stop updating message if it's too long
                    message_too_long_for_telegram = True
                    new_content = f'{new_content[:TELEGRAM_MESSAGE_LENGTH_CUTOFF]} ⏳...'
                await self.message.bot.edit_message_text(new_content, chat_id, message_id, reply_markup=keyboard)
                previous_content = new_content
                previous_time = datetime.now()
        return dialog_message, message_id

    @staticmethod
    def split_dialog_message(dialog_message, max_content_length=TELEGRAM_MESSAGE_LENGTH_CUTOFF):
        """
        Split dialog message into multiple messages if it's too long for telegram
        """
        content = dialog_message.content
        if len(content) <= max_content_length:
            return [dialog_message]

        parts = []
        while len(content) > max_content_length:
            # find last space
            for separator in ['\n', '.', ' ']:
                last_space_index = content.rfind(separator, 0, max_content_length)
                if last_space_index != -1:
                    break
            if last_space_index == -1:
                # no spaces, just split by max_content_length
                parts.append(content[:max_content_length])
                content = content[max_content_length:]
            else:
                parts.append(content[:last_space_index])
                content = content[last_space_index + 1:]
        parts.append(content)
        return [dialog_message.copy(update={"content": part}) for part in parts]
