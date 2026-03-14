import logging
from io import BytesIO
from contextlib import suppress
from typing import Optional, Callable, List

from aiogram import Bot
from aiogram.utils.exceptions import CantParseEntities, BadRequest

from app.context.context_manager import ContextManager
from app.runtime.conversation_session import ConversationSession
from app.runtime.events import (
    StreamingContentDelta, FinalResponse,
    FunctionCallCompleted, ErrorEvent,
)
from app.runtime.runtime import LLMRuntime
from app.runtime.user_input import UserInput
from app.storage.db import User

from app.bot.telegram_runtime_adapter import TelegramRuntimeAdapter, TELEGRAM_MESSAGE_LENGTH_CUTOFF

logger = logging.getLogger(__name__)


class HeadlessSideEffectHandler:
    def __init__(self, bot: Bot, chat_id: int):
        self.bot = bot
        self.chat_id = chat_id

    async def send_message(self, text: str) -> int:
        msg = await self.bot.send_message(self.chat_id, text)
        return msg.message_id

    async def send_photo(self, photo_bytes: bytes, caption: Optional[str] = None) -> int:
        msg = await self.bot.send_photo(self.chat_id, BytesIO(photo_bytes), caption=caption)
        return msg.message_id


async def _send_headless_message(bot: Bot, chat_id: int, text: str, parse_mode=None):
    try:
        return await bot.send_message(chat_id, text, parse_mode=parse_mode)
    except CantParseEntities:
        return await bot.send_message(chat_id, text)


class HeadlessRuntimeAdapter:
    def __init__(self, bot: Bot, user: User, chat_id: int, context_manager: ContextManager):
        self.bot = bot
        self.user = user
        self.chat_id = chat_id
        self.context_manager = context_manager

    async def handle_turn(
        self,
        runtime: LLMRuntime,
        user_input: UserInput,
        session: ConversationSession,
        is_cancelled: Callable[[], bool],
    ) -> dict:
        response_text = ''
        tg_message_ids: List[int] = []

        async for event in runtime.process_turn(user_input, session, is_cancelled):
            if isinstance(event, StreamingContentDelta):
                continue

            elif isinstance(event, FinalResponse):
                final_dialog_message = event.dialog_message
                if final_dialog_message and final_dialog_message.content:
                    dialog_messages = TelegramRuntimeAdapter._split_dialog_message(final_dialog_message)
                    for dm in dialog_messages:
                        resp = await _send_headless_message(
                            self.bot, self.chat_id, dm.content, parse_mode='Markdown'
                        )
                        tg_message_ids.append(resp.message_id)
                        if event.needs_context_save:
                            await self.context_manager.add_message(dm, resp.message_id)
                    response_text = final_dialog_message.content

            elif isinstance(event, FunctionCallCompleted):
                if self.user.function_call_verbose:
                    with suppress(BadRequest):
                        text = f'Function call: {event.function_name}({event.function_args})\n\nResponse: {event.result}'
                        text = text[:TELEGRAM_MESSAGE_LENGTH_CUTOFF]
                        await _send_headless_message(self.bot, self.chat_id, text)

            elif isinstance(event, ErrorEvent):
                error_text = f'Error: {event.message}'
                try:
                    resp = await self.bot.send_message(self.chat_id, error_text)
                    tg_message_ids.append(resp.message_id)
                except Exception:
                    logger.exception("Failed to send error message to chat %s", self.chat_id)

        return {"response_text": response_text, "tg_message_ids": tg_message_ids}
