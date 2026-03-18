from contextlib import suppress
from datetime import datetime
from typing import Callable

from aiogram.types import Message, ParseMode, InlineKeyboardMarkup
from aiogram.utils.exceptions import BadRequest

from app.bot.cancellation_manager import get_cancel_button
from app.bot.utils import send_telegram_message, edit_telegram_message
from app.context.context_manager import ContextManager
from app.runtime.conversation_session import ConversationSession
from app.runtime.events import (
    StreamingContentDelta, FinalResponse,
    FunctionCallStarted, FunctionCallCompleted,
)
from app.runtime.runtime import LLMRuntime
from app.runtime.user_input import UserInput
from app.storage.db import User

WAIT_BETWEEN_MESSAGE_UPDATES = 1
TELEGRAM_MESSAGE_LENGTH_CUTOFF = 4080
THINKING_EMOJI = '\U0001f9e0'
THINKING_MAX_CHARS = 300


def _format_thinking_display(thinking_text: str) -> str:
    thinking_fallback = f'{THINKING_EMOJI} Thinking...'
    if not thinking_text or not thinking_text.strip():
        return thinking_fallback

    lines = thinking_text.strip().split('\n')
    last_line = ''
    for line in reversed(lines):
        if line.strip():
            last_line = line.strip()
            break

    if len(last_line) < 10:
        return thinking_fallback

    if len(last_line) > THINKING_MAX_CHARS:
        last_line = last_line[:THINKING_MAX_CHARS] + '...'

    return f'{THINKING_EMOJI} {last_line}'


class TelegramRuntimeAdapter:
    def __init__(self, message: Message, user: User, context_manager: ContextManager):
        self.message = message
        self.user = user
        self.context_manager = context_manager

    async def handle_turn(
        self,
        runtime: LLMRuntime,
        user_input: UserInput,
        session: ConversationSession,
        is_cancelled: Callable[[], bool],
    ):
        message_id = None
        chat_id = None
        previous_content = None
        previous_time = None
        message_too_long_for_telegram = False
        was_thinking = False

        keyboard = InlineKeyboardMarkup()
        keyboard.add(get_cancel_button())

        final_dialog_message = None

        async for event in runtime.process_turn(user_input, session, is_cancelled):
            if isinstance(event, StreamingContentDelta):
                if message_too_long_for_telegram:
                    continue

                if event.is_thinking:
                    was_thinking = True
                    thinking_display = _format_thinking_display(event.thinking_text)
                    if not message_id:
                        resp = await send_telegram_message(self.message, thinking_display, reply_markup=keyboard)
                        chat_id = self.message.chat.id
                        await self.message.bot.send_chat_action(chat_id, 'typing')
                        message_id = resp.message_id
                        previous_content = thinking_display
                        previous_time = datetime.now()
                        continue

                    time_passed_seconds = (datetime.now() - previous_time).seconds
                    if previous_content != thinking_display and time_passed_seconds >= WAIT_BETWEEN_MESSAGE_UPDATES:
                        await self.message.bot.edit_message_text(thinking_display, chat_id, message_id, reply_markup=keyboard)
                        previous_content = thinking_display
                        previous_time = datetime.now()
                    continue

                # Transition from thinking to normal content
                if was_thinking:
                    was_thinking = False
                    previous_time = None

                new_content = ' '.join(event.visible_text.strip().split(' ')[:-1]) if event.visible_text else ''
                if len(new_content) < 50:
                    continue

                if not message_id:
                    resp = await send_telegram_message(self.message, new_content, reply_markup=keyboard)
                    chat_id = self.message.chat.id
                    await self.message.bot.send_chat_action(chat_id, 'typing')
                    message_id = resp.message_id
                    previous_content = new_content
                    previous_time = datetime.now()
                    continue

                time_passed_seconds = (datetime.now() - previous_time).seconds if previous_time else WAIT_BETWEEN_MESSAGE_UPDATES
                if previous_content != new_content and time_passed_seconds >= WAIT_BETWEEN_MESSAGE_UPDATES:
                    if len(new_content) > TELEGRAM_MESSAGE_LENGTH_CUTOFF:
                        message_too_long_for_telegram = True
                        new_content = f'{new_content[:TELEGRAM_MESSAGE_LENGTH_CUTOFF]} \u23f3...'
                    await self.message.bot.edit_message_text(new_content, chat_id, message_id, reply_markup=keyboard)
                    previous_content = new_content
                    previous_time = datetime.now()

            elif isinstance(event, FinalResponse):
                final_dialog_message = event.dialog_message

                # Delete orphaned thinking message if no content to show (thinking-only + tool_call)
                if message_id is not None and (not final_dialog_message or not final_dialog_message.content):
                    with suppress(BadRequest):
                        await self.message.bot.delete_message(chat_id, message_id)

                if final_dialog_message and final_dialog_message.content:
                    dialog_messages = self._split_dialog_message(final_dialog_message)
                    for dm in dialog_messages:
                        parse_mode = ParseMode.MARKDOWN
                        if message_id is not None:
                            response = await edit_telegram_message(self.message, dm.content, message_id, parse_mode)
                            message_id = None
                        else:
                            response = await send_telegram_message(self.message, dm.content, parse_mode)
                        # Save content message to context with real Telegram message_id
                        if event.needs_context_save:
                            await self.context_manager.add_message(dm, response.message_id)

                # Reset streaming state for next round (after tool calls)
                message_id = None
                message_too_long_for_telegram = False
                was_thinking = False
                previous_content = None
                previous_time = None

            elif isinstance(event, FunctionCallCompleted):
                if self.user.function_call_verbose:
                    with suppress(BadRequest):
                        function_response_text = f'Function call: {event.function_name}({event.function_args})\n\nResponse: {event.result}'
                        function_response_text = function_response_text[:TELEGRAM_MESSAGE_LENGTH_CUTOFF]
                        await send_telegram_message(self.message, function_response_text)

    @staticmethod
    def _split_dialog_message(dialog_message, max_content_length=TELEGRAM_MESSAGE_LENGTH_CUTOFF):
        content = dialog_message.content
        if len(content) <= max_content_length:
            return [dialog_message]

        parts = []
        while len(content) > max_content_length:
            for separator in ['\n', '.', ' ']:
                last_space_index = content.rfind(separator, 0, max_content_length)
                if last_space_index != -1:
                    break
            if last_space_index == -1:
                parts.append(content[:max_content_length])
                content = content[max_content_length:]
            else:
                parts.append(content[:last_space_index])
                content = content[last_space_index + 1:]
        parts.append(content)
        return [dialog_message.copy(update={"content": part}) for part in parts]
