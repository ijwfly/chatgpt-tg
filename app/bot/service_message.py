import asyncio
import logging
import time
from datetime import datetime
from enum import Enum
from typing import Optional

from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

from app.bot.rich_messages import send_rich_message, edit_rich_message, send_rich_draft, with_flood_retry


logger = logging.getLogger(__name__)


class ServiceState(str, Enum):
    IDLE = 'idle'
    THINKING = 'thinking'
    STREAMING = 'streaming'
    STREAMING_OVERFLOW = 'streaming_overflow'
    FUNCTION_HINT = 'function_hint'
    FINAL = 'final'


class SendGate:
    """Paces the live-output Telegram calls of one turn: at most one per `min_interval` seconds, plus a
    hold-off after flood control (`retry_after`). Shared by every live output of the turn, so a new agent
    phase or a draft→service-message fallback does not restart the burst."""

    def __init__(self, min_interval: float = 1.0):
        self.min_interval = min_interval
        self.last_sent: Optional[float] = None
        self.blocked_until: Optional[float] = None

    def delay(self) -> float:
        """Seconds to wait before the next call is allowed (0 = now)."""
        now = time.monotonic()
        wait = 0.0
        if self.last_sent is not None:
            wait = max(wait, self.last_sent + self.min_interval - now)
        if self.blocked_until is not None:
            wait = max(wait, self.blocked_until - now)
        return wait

    def mark_sent(self) -> None:
        self.last_sent = time.monotonic()

    def block(self, seconds: float) -> None:
        until = time.monotonic() + seconds
        self.blocked_until = max(self.blocked_until or 0.0, until)


async def _cancel_task(task: Optional[asyncio.Task]) -> None:
    if task is None or task is asyncio.current_task():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


class _PacedOutput:
    """Trailing-edge throttle shared by the live outputs.

    A state change is delivered right away when the gate allows it; otherwise the latest text is kept
    as `_pending` and a single flush task delivers it as soon as the gate opens — nothing is dropped and
    nothing bursts. Flood control (`TelegramRetryAfter`) just extends the gate: the text stays pending.
    """

    def __init__(self, gate: SendGate):
        self.gate = gate
        self.current_text: Optional[str] = None
        self.last_send_time: Optional[datetime] = None
        self._pending: Optional[tuple] = None
        self._flush_task: Optional[asyncio.Task] = None

    def _accepting(self) -> bool:
        raise NotImplementedError

    async def _deliver_now(self, text: str, reply_markup) -> None:
        raise NotImplementedError

    async def _request(self, text: str, reply_markup=None, throttle_seconds: float = 0) -> None:
        if not self._accepting():
            return
        if text == self.current_text:
            self._pending = None
            return
        wait = self.gate.delay()
        if throttle_seconds > 0 and self.last_send_time is not None:
            wait = max(wait, throttle_seconds - (datetime.now() - self.last_send_time).total_seconds())
        if wait <= 0:
            self._pending = None
            await self._deliver_now(text, reply_markup)
        else:
            self._pending = (text, reply_markup)
            self._ensure_flush(wait)

    def _ensure_flush(self, wait: float) -> None:
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._flush(wait))

    async def _flush(self, wait: float) -> None:
        await asyncio.sleep(wait)
        while self._pending is not None and self._accepting():
            wait = self.gate.delay()
            if wait > 0:
                await asyncio.sleep(wait)
                continue
            text, reply_markup = self._pending
            self._pending = None
            if text != self.current_text:
                await self._deliver_now(text, reply_markup)

    def _flood_control(self, text: str, reply_markup, error: TelegramRetryAfter) -> None:
        logger.warning(
            'Flood control on %s in chat %s, holding live output for %s s',
            error.method.__api_method__, getattr(self, 'chat_id', '?'), error.retry_after,
        )
        self.gate.block(error.retry_after)
        self._pending = (text, reply_markup)
        self._ensure_flush(error.retry_after)

    def _mark_delivered(self, text: str) -> None:
        self.current_text = text
        self.last_send_time = datetime.now()

    async def cancel_pending(self) -> None:
        """Drops the pending update and stops the flush task (nothing may land after the final answer)."""
        self._pending = None
        await _cancel_task(self._flush_task)
        self._flush_task = None


class ChatServiceMessage(_PacedOutput):
    """One bot message kept attached to a chat across an LLM turn.

    Provides set_text/finalize/clear helpers that prefer in-place edits
    over send/delete cycles, with deduplication, pacing through the shared
    SendGate, and TelegramBadRequest recovery. Text is rich markdown
    (plain-text fallback lives in the rich_messages helpers).

    Also implements the live-output interface shared with DraftStream
    (set_thinking / set_hint / set_content / finish): intermediate states
    carry `stream_markup` (the Stop button), the finished answer has no keyboard.
    """

    def __init__(self, message: Message, stream_markup=None, gate: Optional[SendGate] = None):
        super().__init__(gate or SendGate())
        self.message = message
        self.chat_id: int = message.chat.id
        self.stream_markup = stream_markup
        self.message_id: Optional[int] = None
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

    def _accepting(self) -> bool:
        return not (self.frozen or self.detached)

    async def set_text(
        self,
        text: str,
        *,
        reply_markup=None,
        throttle_seconds: float = 0,
    ) -> Optional[int]:
        """Send or edit the service message in place (now, or as soon as the gate allows).

        Returns the current message_id (None while nothing has been sent yet).
        Skips silently if frozen / detached / dedup matches.
        """
        await self._request(text, reply_markup, throttle_seconds)
        return self.message_id

    async def _deliver_now(self, text: str, reply_markup) -> None:
        self.gate.mark_sent()
        if self.message_id is None:
            try:
                response = await send_rich_message(self.message, text, reply_markup=reply_markup)
            except TelegramRetryAfter as e:
                self._flood_control(text, reply_markup, e)
                return
            except TelegramBadRequest as e:
                logger.warning("Failed to send service message: %s", e)
                return
            self.message_id = response.message_id
        else:
            try:
                await edit_rich_message(self.message, text, self.message_id, reply_markup=reply_markup)
            except TelegramRetryAfter as e:
                self._flood_control(text, reply_markup, e)
                return
            except TelegramBadRequest as e:
                logger.warning(
                    "Failed to edit service message %s: %s; invalidating",
                    self.message_id, e,
                )
                self.message_id = None
                self.current_text = None
                return
        self._mark_delivered(text)

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
        await self.cancel_pending()

        if self.message_id is None:
            try:
                response = await with_flood_retry(
                    lambda: send_rich_message(self.message, text, reply_markup=reply_markup)
                )
                self.message_id = response.message_id
            except TelegramBadRequest as e:
                logger.warning("Failed to send finalized service message: %s", e)
                return None
        else:
            try:
                await with_flood_retry(
                    lambda: edit_rich_message(self.message, text, self.message_id, reply_markup=reply_markup)
                )
            except TelegramBadRequest as e:
                logger.warning(
                    "Failed to edit service message %s on finalize: %s; sending fresh",
                    self.message_id, e,
                )
                self.message_id = None
                try:
                    response = await with_flood_retry(
                        lambda: send_rich_message(self.message, text, reply_markup=reply_markup)
                    )
                    self.message_id = response.message_id
                except TelegramBadRequest as e2:
                    logger.warning("Failed to send replacement finalized message: %s", e2)
                    return None

        self._mark_delivered(text)
        self.detached = True
        return self.message_id

    def freeze(self) -> None:
        self.frozen = True

    async def clear(self) -> None:
        """Delete the service message if still attached and not detached."""
        await self.cancel_pending()
        if self.message_id is None or self.detached:
            return
        try:
            await self.message.bot.delete_message(chat_id=self.chat_id, message_id=self.message_id)
        except TelegramBadRequest as e:
            logger.warning("Failed to delete service message %s: %s", self.message_id, e)
        self.message_id = None
        self.current_text = None


class DraftStream(_PacedOutput):
    """Streams a turn as an ephemeral rich draft (`sendRichMessageDraft`, private chats only).

    Drafts are animated by the client, support `<tg-thinking>` and disappear by themselves after ~30 s
    or when the bot sends a message, so the finished answer is always a fresh `sendRichMessage`.
    Drafts cannot carry an inline keyboard: the Stop button is Telegram's native one (`can_stop`).
    A keepalive re-sends the last draft while a long tool call keeps the turn silent. When Telegram
    rejects a draft call, `failed` is set and the adapter continues the turn with a ChatServiceMessage.
    """

    KEEPALIVE_SECONDS = 20

    def __init__(self, message: Message, draft_id: int, can_stop: bool = True, gate: Optional[SendGate] = None):
        super().__init__(gate or SendGate())
        self.message = message
        self.chat_id: int = message.chat.id
        self.draft_id = draft_id
        self.can_stop = can_stop
        self.frozen: bool = False
        self.failed: bool = False
        self._keepalive_task: Optional[asyncio.Task] = None

    @property
    def needs_cleanup(self) -> bool:
        return False  # nothing persists in the chat

    async def set_thinking(self, text: str, throttle_seconds: float = 0) -> None:
        await self._request(f'<tg-thinking>{text}</tg-thinking>', None, throttle_seconds)

    async def set_hint(self, text: str) -> None:
        await self._request(f'<tg-thinking>{text}</tg-thinking>', None, 0)

    async def set_content(self, markdown: str, throttle_seconds: float = 0) -> None:
        await self._request(markdown, None, throttle_seconds)

    async def finish(self, markdown: str) -> Optional[int]:
        """Sends the finished answer as a real message (the draft vanishes on its own)."""
        await self.clear()
        try:
            response = await with_flood_retry(lambda: send_rich_message(self.message, markdown))
        except TelegramBadRequest as e:
            logger.warning("Failed to send finished answer after draft stream: %s", e)
            return None
        return response.message_id

    def freeze(self) -> None:
        self.frozen = True

    def _accepting(self) -> bool:
        return not (self.frozen or self.failed)

    async def clear(self) -> None:
        """Stops the keepalive and any pending update; drafts leave nothing to delete."""
        await self.cancel_pending()
        await _cancel_task(self._keepalive_task)
        self._keepalive_task = None

    async def _deliver_now(self, text: str, reply_markup=None) -> None:
        self.gate.mark_sent()
        try:
            await send_rich_draft(self.message.bot, self.chat_id, self.draft_id, text, can_stop=self.can_stop)
        except TelegramRetryAfter as e:
            self._flood_control(text, None, e)
            return
        except TelegramBadRequest as e:
            logger.warning("Draft %s rejected, falling back to a service message: %s", self.draft_id, e)
            self.failed = True
            await self.clear()
            return
        self._mark_delivered(text)
        if self._keepalive_task is None:
            self._keepalive_task = asyncio.create_task(self._keepalive())

    async def _keepalive(self) -> None:
        while True:
            await asyncio.sleep(self.KEEPALIVE_SECONDS)
            if self.frozen or self.failed or self.current_text is None or self._pending is not None:
                continue
            if (datetime.now() - self.last_send_time).total_seconds() < self.KEEPALIVE_SECONDS:
                continue
            if self.gate.delay() > 0:
                continue
            await self._deliver_now(self.current_text)
