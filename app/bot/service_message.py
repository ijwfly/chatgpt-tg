import logging
from datetime import datetime
from enum import Enum
from typing import Optional

from aiogram.types import Message
from aiogram.utils.exceptions import BadRequest

from app.bot.utils import send_telegram_message, edit_telegram_message


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
    BadRequest recovery.
    """

    def __init__(self, message: Message):
        self.message = message
        self.chat_id: int = message.chat.id
        self.message_id: Optional[int] = None
        self.current_text: Optional[str] = None
        self.last_send_time: Optional[datetime] = None
        self.frozen: bool = False
        self.detached: bool = False

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
        parse_mode: Optional[str] = None,
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
                response = await send_telegram_message(
                    self.message, text, parse_mode=parse_mode, reply_markup=reply_markup,
                )
                self.message_id = response.message_id
                self.current_text = text
                self.last_send_time = datetime.now()
            except BadRequest as e:
                logger.warning("Failed to send service message: %s", e)
        else:
            try:
                await edit_telegram_message(
                    self.message, text, self.message_id,
                    parse_mode=parse_mode, reply_markup=reply_markup,
                )
                self.current_text = text
                self.last_send_time = datetime.now()
            except BadRequest as e:
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
        parse_mode: Optional[str] = None,
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
                response = await send_telegram_message(
                    self.message, text, parse_mode=parse_mode, reply_markup=reply_markup,
                )
                self.message_id = response.message_id
            except BadRequest as e:
                logger.warning("Failed to send finalized service message: %s", e)
                return None
        else:
            try:
                await edit_telegram_message(
                    self.message, text, self.message_id,
                    parse_mode=parse_mode, reply_markup=reply_markup,
                )
            except BadRequest as e:
                logger.warning(
                    "Failed to edit service message %s on finalize: %s; sending fresh",
                    self.message_id, e,
                )
                self.message_id = None
                try:
                    response = await send_telegram_message(
                        self.message, text, parse_mode=parse_mode, reply_markup=reply_markup,
                    )
                    self.message_id = response.message_id
                except BadRequest as e2:
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
            await self.message.bot.delete_message(self.chat_id, self.message_id)
        except BadRequest as e:
            logger.warning("Failed to delete service message %s: %s", self.message_id, e)
        self.message_id = None
        self.current_text = None
