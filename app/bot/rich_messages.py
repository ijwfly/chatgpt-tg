"""Telegram Rich Messages (Bot API 10.1+) transport helpers.

LLM answers and menus are sent as `InputRichMessage(markdown=...)`: GitHub-flavoured markdown rendered by
Telegram itself (headings, tables, code fences, LaTeX, ...), up to 32768 characters. Every helper falls back
to a plain-text `sendMessage` when Telegram rejects the markup, so a formatting glitch never loses an answer.
See specs/RICH_MESSAGES.md.
"""
import logging
import re
from typing import List, Optional

from aiogram import Bot, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputRichMessage, ReplyParameters

from app.bot.utils import is_parse_error

logger = logging.getLogger(__name__)

# Telegram allows 32768 characters in a rich message; keep headroom for fence re-opening and entity expansion
RICH_MESSAGE_LENGTH_CUTOFF = 30000

_FENCE_RE = re.compile(r'^\s{0,3}(`{3,}|~{3,})(.*)$')


def rich(markdown: str) -> InputRichMessage:
    return InputRichMessage(markdown=markdown)


def _reply_parameters(message: types.Message) -> Optional[ReplyParameters]:
    """Same rule as utils.send_telegram_message: reply only when the user's message is itself a reply."""
    if message.reply_to_message is None:
        return None
    return ReplyParameters(message_id=message.message_id)


async def send_rich_message(message: types.Message, markdown: str, reply_markup=None) -> types.Message:
    """Sends `markdown` as a rich message in the chat of `message`; plain text if Telegram can't parse it."""
    return await send_rich_message_to_chat(
        message.bot, message.chat.id, markdown,
        reply_parameters=_reply_parameters(message), reply_markup=reply_markup,
    )


async def send_rich_message_to_chat(
    bot: Bot, chat_id: int, markdown: str, reply_parameters: Optional[ReplyParameters] = None, reply_markup=None,
) -> types.Message:
    try:
        return await bot.send_rich_message(
            chat_id=chat_id, rich_message=rich(markdown),
            reply_parameters=reply_parameters, reply_markup=reply_markup,
        )
    except TelegramBadRequest as e:
        if not is_parse_error(e):
            raise
        logger.warning('Rich message rejected, sending as plain text: %s', e.message)
        return await bot.send_message(
            chat_id=chat_id, text=markdown, parse_mode=None,
            reply_parameters=reply_parameters, reply_markup=reply_markup,
        )


async def edit_rich_message(message: types.Message, markdown: str, message_id: int, reply_markup=None):
    return await edit_rich_message_in_chat(message.bot, message.chat.id, message_id, markdown, reply_markup)


async def edit_rich_message_in_chat(bot: Bot, chat_id: int, message_id: int, markdown: str, reply_markup=None):
    try:
        return await bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, rich_message=rich(markdown), reply_markup=reply_markup,
        )
    except TelegramBadRequest as e:
        if not is_parse_error(e):
            raise
        logger.warning('Rich edit rejected, editing as plain text: %s', e.message)
        return await bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=markdown, parse_mode=None, reply_markup=reply_markup,
        )


async def send_rich_draft(bot: Bot, chat_id: int, draft_id: int, markdown: str, can_stop: bool = True) -> bool:
    """Streams an ephemeral draft (private chats only). The final text must still be sent as a message.

    `can_stop` shows the native Stop button; pressing it delivers a `stopped_message_generation` update.
    """
    return await bot.send_rich_message_draft(
        chat_id=chat_id, draft_id=draft_id, rich_message=rich(markdown), can_stop=can_stop,
    )


def escape_rich_markdown(text: str) -> str:
    """Escapes user-supplied text for embedding into rich markdown (which may also contain HTML)."""
    specials = '\\*_`[]#~=|!'
    escaped = ''.join('\\' + char if char in specials else char for char in text)
    return escaped.replace('<', '&lt;').replace('>', '&gt;')


def _open_fence_at(text: str) -> Optional[str]:
    """Returns the opening fence line (e.g. '```python') if `text` ends inside a code block, else None."""
    open_fence = None
    for line in text.split('\n'):
        match = _FENCE_RE.match(line)
        if not match:
            continue
        marker, info = match.group(1), match.group(2).strip()
        if open_fence is None:
            open_fence = (marker, info)
        elif marker[0] == open_fence[0][0] and len(marker) >= len(open_fence[0]) and not info:
            open_fence = None
    if open_fence is None:
        return None
    return open_fence[0] + open_fence[1]


def split_markdown(content: str, max_len: int = RICH_MESSAGE_LENGTH_CUTOFF) -> List[str]:
    """Greedy split at newline / sentence / space boundaries; code fences are closed and re-opened at cuts."""
    if len(content) <= max_len:
        return [content]

    parts = []
    while len(content) > max_len:
        # reserve room for a closing fence line
        cut_limit = max_len - 4
        for separator in ['\n', '.', ' ']:
            cut = content.rfind(separator, 0, cut_limit)
            if cut > 0:
                break
        if cut <= 0:
            head, content = content[:cut_limit], content[cut_limit:]
        else:
            head, content = content[:cut], content[cut + 1:]

        fence = _open_fence_at(head)
        if fence is not None:
            head = head + '\n' + fence[0] * 3
            content = fence + '\n' + content
        parts.append(head)
    parts.append(content)
    return parts
