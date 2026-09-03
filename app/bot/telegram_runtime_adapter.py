from contextlib import suppress
from typing import Callable

from aiogram.enums import ChatAction, ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import settings
from app.bot.cancellation_manager import get_cancel_button
from app.bot.rich_messages import (
    RICH_MESSAGE_LENGTH_CUTOFF, escape_rich_markdown, send_rich_message, split_markdown,
)
from app.bot.service_message import ChatServiceMessage, DraftStream, ServiceState
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
HINT_MAX_CHARS = 300
MIN_STREAMING_CONTENT_LEN = 50


def _format_hint(hint_text: str) -> str:
    """Tool status hints carry model-supplied text (queries, commands, urls), so they are
    collapsed to one line, capped and escaped before going into rich markdown."""
    text = ' '.join(hint_text.split())
    if len(text) > HINT_MAX_CHARS:
        text = text[:HINT_MAX_CHARS] + '...'
    return escape_rich_markdown(text)


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
        self._phase = 0

    def _stream_markup(self):
        return InlineKeyboardBuilder().add(get_cancel_button()).as_markup()

    def _new_live_output(self):
        """Draft stream in private chats when settings.RICH_DRAFT_STREAMING is on (native Stop button);
        otherwise a real service message edited in place.

        Each agent phase gets its own draft id so consecutive answers animate independently.
        """
        self._phase += 1
        if settings.RICH_DRAFT_STREAMING and self.message.chat.type == ChatType.PRIVATE:
            return DraftStream(self.message, draft_id=self.message.message_id * 100 + self._phase)
        return ChatServiceMessage(self.message, stream_markup=self._stream_markup())

    def _fallback_live_output(self):
        return ChatServiceMessage(self.message, stream_markup=self._stream_markup())

    async def handle_turn(
        self,
        runtime: LLMRuntime,
        user_input: UserInput,
        session: ConversationSession,
        is_cancelled: Callable[[], bool],
    ):
        live = self._new_live_output()
        state = ServiceState.IDLE
        typing_action_sent = False

        async def ensure_typing_action():
            nonlocal typing_action_sent
            if not typing_action_sent:
                with suppress(TelegramBadRequest):
                    await self.message.bot.send_chat_action(chat_id=self.message.chat.id, action=ChatAction.TYPING)
                typing_action_sent = True

        async def show(coro):
            """Runs a live-output update; if the draft transport failed, redo it on a service message."""
            nonlocal live
            await coro
            if live.failed:
                live = self._fallback_live_output()

        try:
            async for event in runtime.process_turn(user_input, session, is_cancelled):
                if isinstance(event, StreamingContentDelta):
                    if state == ServiceState.STREAMING_OVERFLOW:
                        continue

                    if event.is_thinking:
                        thinking_display = _format_thinking_display(event.thinking_text)
                        throttle = WAIT_BETWEEN_MESSAGE_UPDATES if state == ServiceState.THINKING else 0
                        await show(live.set_thinking(thinking_display, throttle_seconds=throttle))
                        state = ServiceState.THINKING
                        await ensure_typing_action()
                        continue

                    new_content = ' '.join(event.visible_text.strip().split(' ')[:-1]) if event.visible_text else ''
                    if len(new_content) < MIN_STREAMING_CONTENT_LEN:
                        continue

                    if len(new_content) > TELEGRAM_MESSAGE_LENGTH_CUTOFF:
                        truncated = f'{new_content[:TELEGRAM_MESSAGE_LENGTH_CUTOFF]} ⏳...'
                        await show(live.set_content(truncated))
                        live.freeze()
                        state = ServiceState.STREAMING_OVERFLOW
                        await ensure_typing_action()
                        continue

                    throttle = WAIT_BETWEEN_MESSAGE_UPDATES if state == ServiceState.STREAMING else 0
                    await show(live.set_content(new_content, throttle_seconds=throttle))
                    state = ServiceState.STREAMING
                    await ensure_typing_action()

                elif isinstance(event, FinalResponse):
                    final_dialog_message = event.dialog_message

                    if final_dialog_message and final_dialog_message.content:
                        dialog_messages = self._split_dialog_message(
                            final_dialog_message, TELEGRAM_MESSAGE_LENGTH_CUTOFF,
                        )
                        first, rest = dialog_messages[0], dialog_messages[1:]

                        first_id = await live.finish(first.content)
                        if event.needs_context_save and first_id is not None:
                            await self.context_manager.add_message(first, first_id)

                        for dm in rest:
                            response = await send_rich_message(self.message, dm.content)
                            if event.needs_context_save:
                                await self.context_manager.add_message(dm, response.message_id)

                        # The answer is out; any following phase (e.g. another agent
                        # iteration) starts with a fresh live output.
                        live = self._new_live_output()
                        typing_action_sent = False
                        state = ServiceState.IDLE
                    else:
                        # Tool-only / empty response — keep the live output for the next event.
                        pass

                elif isinstance(event, FunctionCallStarted):
                    if self.user.function_call_hints:
                        hint_text = event.status_message or f'Running {event.function_name}...'
                        await show(live.set_hint(_format_hint(hint_text)))
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
                    and live.needs_cleanup:
                await live.clear()
            else:
                # drafts: stop the keepalive; service messages: no-op when nothing is attached
                with suppress(TelegramBadRequest):
                    if isinstance(live, DraftStream):
                        await live.clear()

    @staticmethod
    def _split_dialog_message(dialog_message, max_content_length):
        parts = split_markdown(dialog_message.content, max_content_length)
        return [dialog_message.model_copy(update={"content": part}) for part in parts]
