import asyncio
import logging
from datetime import datetime
from enum import Enum
from typing import Optional

from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest

from app.bot.rich_messages import send_rich_message, edit_rich_message, send_rich_draft


logger = logging.getLogger(__name__)


class ServiceState(str, Enum):
    IDLE = 'idle'
    THINKING = 'thinking'
    STREAMING = 'streaming'
    STREAMING_OVERFLOW = 'streaming_overflow'
    FUNCTION_HINT = 'function_hint'
    FINAL = 'final'


class ChatServiceMessage:
    """One bot message kept attached to a chat across an LLM turn.

    Provides set_text/finalize/clear helpers that prefer in-place edits
    over send/delete cycles, with deduplication, optional throttling, and
    TelegramBadRequest recovery. Text is rich markdown (plain-text fallback
    lives in the rich_messages helpers).

    Also implements the live-output interface shared with DraftStream
    (set_thinking / set_hint / set_content / finish): intermediate states
    carry `stream_markup` (the Stop button), the finished answer has no keyboard.
    """

    def __init__(self, message: Message, stream_markup=None):
        self.message = message
        self.chat_id: int = message.chat.id
        self.stream_markup = stream_markup
        self.message_id: Optional[int] = None
        self.current_text: Optional[str] = None
        self.last_send_time: Optional[datetime] = None
        self.frozen: bool = False
        self.detached: bool = False
        self.failed: bool = False  # never set: a service message has no cheaper fallback

    # -- live-output interface --

    async def set_thinking(self, text: str, throttle_seconds: float = 0) -> None:
        await self.set_text(text, reply_markup=self.stream_markup, throttle_seconds=throttle_seconds)

    async def set_hint(self, text: str) -> None:
        await self.set_text(text, reply_markup=self.stream_markup, throttle_seconds=0)

    async def set_content(self, markdown: str, throttle_seconds: float = 0) -> None:
        await self.set_text(markdown, reply_markup=self.stream_markup, throttle_seconds=throttle_seconds)

    async def finish(self, markdown: str) -> Optional[int]:
        """The first chunk of the answer is edited into the service message; returns its telegram id."""
        return await self.finalize(markdown, reply_markup=None)

    @property
    def needs_cleanup(self) -> bool:
        return self.is_attached and not self.is_detached

    @property
    def is_attached(self) -> bool:
        return self.message_id is not None

    @property
    def is_detached(self) -> bool:
        return self.detached

    async def set_text(
        self,
        text: str,
        *,
        reply_markup=None,
        throttle_seconds: float = 0,
    ) -> Optional[int]:
        """Send or edit the service message in place.

        Returns the message_id, or None if both send and edit failed.
        Skips silently if frozen / detached / dedup matches / throttled.
        """
        if self.frozen or self.detached:
            return self.message_id

        if self.current_text == text:
            return self.message_id

        if throttle_seconds > 0 and self.last_send_time is not None:
            elapsed = (datetime.now() - self.last_send_time).total_seconds()
            if elapsed < throttle_seconds:
                return self.message_id

        if self.message_id is None:
            try:
                response = await send_rich_message(self.message, text, reply_markup=reply_markup)
                self.message_id = response.message_id
                self.current_text = text
                self.last_send_time = datetime.now()
            except TelegramBadRequest as e:
                logger.warning("Failed to send service message: %s", e)
        else:
            try:
                await edit_rich_message(self.message, text, self.message_id, reply_markup=reply_markup)
                self.current_text = text
                self.last_send_time = datetime.now()
            except TelegramBadRequest as e:
                logger.warning(
                    "Failed to edit service message %s: %s; invalidating",
                    self.message_id, e,
                )
                self.message_id = None
                self.current_text = None

        return self.message_id

    async def finalize(
        self,
        text: str,
        *,
        reply_markup=None,
    ) -> Optional[int]:
        """Write the final answer into the service message and detach.

        After finalize the instance no longer touches the message
        (subsequent set_text/clear become no-ops).
        """
        if self.detached:
            return self.message_id

        if self.message_id is None:
            try:
                response = await send_rich_message(self.message, text, reply_markup=reply_markup)
                self.message_id = response.message_id
            except TelegramBadRequest as e:
                logger.warning("Failed to send finalized service message: %s", e)
                return None
        else:
            try:
                await edit_rich_message(self.message, text, self.message_id, reply_markup=reply_markup)
            except TelegramBadRequest as e:
                logger.warning(
                    "Failed to edit service message %s on finalize: %s; sending fresh",
                    self.message_id, e,
                )
                self.message_id = None
                try:
                    response = await send_rich_message(self.message, text, reply_markup=reply_markup)
                    self.message_id = response.message_id
                except TelegramBadRequest as e2:
                    logger.warning("Failed to send replacement finalized message: %s", e2)
                    return None

        self.current_text = text
        self.last_send_time = datetime.now()
        self.detached = True
        return self.message_id

    def freeze(self) -> None:
        self.frozen = True

    async def clear(self) -> None:
        """Delete the service message if still attached and not detached."""
        if self.message_id is None or self.detached:
            return
        try:
            await self.message.bot.delete_message(chat_id=self.chat_id, message_id=self.message_id)
        except TelegramBadRequest as e:
            logger.warning("Failed to delete service message %s: %s", self.message_id, e)
        self.message_id = None
        self.current_text = None


class DraftStream:
    """Streams a turn as an ephemeral rich draft (`sendRichMessageDraft`, private chats only).

    Drafts are animated by the client, support `<tg-thinking>` and disappear by themselves after ~30 s
    or when the bot sends a message, so the finished answer is always a fresh `sendRichMessage`.
    A keepalive re-sends the last draft while a long tool call keeps the turn silent. When Telegram
    rejects a draft call, `failed` is set and the adapter continues the turn with a ChatServiceMessage.
    """

    KEEPALIVE_SECONDS = 20

    def __init__(self, message: Message, draft_id: int, can_stop: bool = True):
        self.message = message
        self.chat_id: int = message.chat.id
        self.draft_id = draft_id
        self.can_stop = can_stop
        self.current_text: Optional[str] = None
        self.last_send_time: Optional[datetime] = None
        self.frozen: bool = False
        self.failed: bool = False
        self._keepalive_task: Optional[asyncio.Task] = None

    @property
    def needs_cleanup(self) -> bool:
        return False  # nothing persists in the chat

    async def set_thinking(self, text: str, throttle_seconds: float = 0) -> None:
        await self._send(f'<tg-thinking>{text}</tg-thinking>', throttle_seconds)

    async def set_hint(self, text: str) -> None:
        await self._send(f'<tg-thinking>{text}</tg-thinking>', 0)

    async def set_content(self, markdown: str, throttle_seconds: float = 0) -> None:
        await self._send(markdown, throttle_seconds)

    async def finish(self, markdown: str) -> Optional[int]:
        """Sends the finished answer as a real message (the draft vanishes on its own)."""
        await self.clear()
        try:
            response = await send_rich_message(self.message, markdown)
        except TelegramBadRequest as e:
            logger.warning("Failed to send finished answer after draft stream: %s", e)
            return None
        return response.message_id

    def freeze(self) -> None:
        self.frozen = True

    async def clear(self) -> None:
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass
            self._keepalive_task = None

    async def _send(self, text: str, throttle_seconds: float) -> None:
        if self.frozen or self.failed or self.current_text == text:
            return
        if throttle_seconds > 0 and self.last_send_time is not None:
            if (datetime.now() - self.last_send_time).total_seconds() < throttle_seconds:
                return
        if not await self._deliver(text):
            return
        self.current_text = text
        if self._keepalive_task is None:
            self._keepalive_task = asyncio.create_task(self._keepalive())

    async def _deliver(self, text: str) -> bool:
        try:
            await send_rich_draft(self.message.bot, self.chat_id, self.draft_id, text, can_stop=self.can_stop)
        except TelegramBadRequest as e:
            logger.warning("Draft %s rejected, falling back to a service message: %s", self.draft_id, e)
            self.failed = True
            await self.clear()
            return False
        self.last_send_time = datetime.now()
        return True

    async def _keepalive(self) -> None:
        while True:
            await asyncio.sleep(self.KEEPALIVE_SECONDS)
            if self.frozen or self.current_text is None:
                continue
            if (datetime.now() - self.last_send_time).total_seconds() < self.KEEPALIVE_SECONDS:
                continue
            if not await self._deliver(self.current_text):
                return
