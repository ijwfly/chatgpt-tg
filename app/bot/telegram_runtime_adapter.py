from contextlib import suppress
from typing import Callable

from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.cancellation_manager import get_cancel_button
from app.bot.rich_messages import RICH_MESSAGE_LENGTH_CUTOFF, send_rich_message, split_markdown
from app.bot.service_message import ChatServiceMessage, ServiceState
from app.bot.utils import send_telegram_message
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
# rich messages allow 32768 characters; verbose tool output still goes through plain sendMessage
TELEGRAM_MESSAGE_LENGTH_CUTOFF = RICH_MESSAGE_LENGTH_CUTOFF
PLAIN_MESSAGE_LENGTH_CUTOFF = 4080
THINKING_EMOJI = '\U0001f9e0'
THINKING_MAX_CHARS = 300
MIN_STREAMING_CONTENT_LEN = 50


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
        service = ChatServiceMessage(self.message)
        state = ServiceState.IDLE
        typing_action_sent = False

        keyboard = InlineKeyboardBuilder().add(get_cancel_button()).as_markup()

        async def ensure_typing_action():
            nonlocal typing_action_sent
            if not typing_action_sent and service.is_attached:
                with suppress(TelegramBadRequest):
                    await self.message.bot.send_chat_action(chat_id=service.chat_id, action=ChatAction.TYPING)
                typing_action_sent = True

        try:
            async for event in runtime.process_turn(user_input, session, is_cancelled):
                if isinstance(event, StreamingContentDelta):
                    if state == ServiceState.STREAMING_OVERFLOW:
                        continue

                    if event.is_thinking:
                        thinking_display = _format_thinking_display(event.thinking_text)
                        throttle = WAIT_BETWEEN_MESSAGE_UPDATES if state == ServiceState.THINKING else 0
                        await service.set_text(
                            thinking_display,
                            reply_markup=keyboard,
                            throttle_seconds=throttle,
                        )
                        state = ServiceState.THINKING
                        await ensure_typing_action()
                        continue

                    new_content = ' '.join(event.visible_text.strip().split(' ')[:-1]) if event.visible_text else ''
                    if len(new_content) < MIN_STREAMING_CONTENT_LEN:
                        continue

                    if len(new_content) > TELEGRAM_MESSAGE_LENGTH_CUTOFF:
                        truncated = f'{new_content[:TELEGRAM_MESSAGE_LENGTH_CUTOFF]} ⏳...'
                        await service.set_text(truncated, reply_markup=keyboard)
                        service.freeze()
                        state = ServiceState.STREAMING_OVERFLOW
                        await ensure_typing_action()
                        continue

                    throttle = WAIT_BETWEEN_MESSAGE_UPDATES if state == ServiceState.STREAMING else 0
                    await service.set_text(
                        new_content,
                        reply_markup=keyboard,
                        throttle_seconds=throttle,
                    )
                    state = ServiceState.STREAMING
                    await ensure_typing_action()

                elif isinstance(event, FinalResponse):
                    final_dialog_message = event.dialog_message

                    if final_dialog_message and final_dialog_message.content:
                        dialog_messages = self._split_dialog_message(
                            final_dialog_message, TELEGRAM_MESSAGE_LENGTH_CUTOFF,
                        )
                        first, rest = dialog_messages[0], dialog_messages[1:]

                        finalize_id = await service.finalize(first.content, reply_markup=None)
                        if event.needs_context_save and finalize_id is not None:
                            await self.context_manager.add_message(first, finalize_id)

                        for dm in rest:
                            response = await send_rich_message(self.message, dm.content)
                            if event.needs_context_save:
                                await self.context_manager.add_message(dm, response.message_id)

                        # Detach the finalized message and start fresh for any
                        # following phase (e.g. another agent iteration).
                        service = ChatServiceMessage(self.message)
                        typing_action_sent = False
                        state = ServiceState.IDLE
                    else:
                        # Tool-only / empty response — keep service alive for next event.
                        pass

                elif isinstance(event, FunctionCallStarted):
                    if self.user.function_call_hints:
                        hint_text = event.status_message or f'Running {event.function_name}...'
                        await service.set_text(
                            hint_text,
                            reply_markup=keyboard,
                            throttle_seconds=0,
                        )
                        state = ServiceState.FUNCTION_HINT
                        await ensure_typing_action()

                elif isinstance(event, FunctionCallCompleted):
                    if self.user.function_call_verbose:
                        with suppress(TelegramBadRequest):
                            text = (
                                f'Function call: {event.function_name}({event.function_args})'
                                f'\n\nResponse: {event.result}'
                            )
                            text = text[:PLAIN_MESSAGE_LENGTH_CUTOFF]
                            await send_telegram_message(self.message, text)
        finally:
            if state in (ServiceState.THINKING, ServiceState.STREAMING, ServiceState.FUNCTION_HINT) \
                    and service.is_attached and not service.is_detached:
                await service.clear()

    @staticmethod
    def _split_dialog_message(dialog_message, max_content_length):
        parts = split_markdown(dialog_message.content, max_content_length)
        return [dialog_message.model_copy(update={"content": part}) for part in parts]
