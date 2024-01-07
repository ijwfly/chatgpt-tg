import dataclasses
import re
import asyncio
from datetime import date
from typing import List
from contextlib import asynccontextmanager

from aiogram import types
from aiogram.utils.exceptions import CantParseEntities

from app.openai_helpers.utils import (calculate_completion_usage_price, calculate_whisper_usage_price,
                                      calculate_image_generation_usage_price, calculate_tts_usage_price)

TYPING_TIMEOUT = 180
TYPING_DELAY = 2
TYPING_QUERIES_LIMIT = TYPING_TIMEOUT // TYPING_DELAY


class TypingWorker:
    ACTION_TYPING = 'typing'
    ACTION_UPLOAD_PHOTO = 'upload_photo'
    ACTION_UPLOAD_DOCUMENT = 'upload_document'
    ACTION_RECORD_VOICE = 'record_voice'

    def __init__(self, bot, chat_id, action='typing'):
        self.bot = bot
        self.chat_id = chat_id
        self.action = action
        self.typing_task = None
        self.typing_queries_count = 0

    @asynccontextmanager
    async def typing_context(self):
        try:
            await self.start_typing()
            yield
        finally:
            await self.stop_typing()

    async def start_typing(self):
        async def typing_worker():
            while self.typing_queries_count < TYPING_QUERIES_LIMIT:
                await self.bot.send_chat_action(self.chat_id, self.action)
                await asyncio.sleep(TYPING_DELAY)
                self.typing_queries_count += 1

        self.typing_task = asyncio.create_task(typing_worker())
        return self

    async def stop_typing(self):
        if self.typing_task is None:
            return

        self.typing_task.cancel()
        try:
            await self.typing_task
        except asyncio.CancelledError:
            pass
        self.typing_task = None


class Timer:
    """
    Async timer with reset method
    """
    def __init__(self, timeout=0.3):
        self.timeout = timeout
        self._current_timeout = timeout
        self.step = timeout / 100

    async def sleep(self):
        while True:
            await asyncio.sleep(self.step)
            self._current_timeout -= self.step
            if self._current_timeout <= 0:
                break

    def reset(self):
        self._current_timeout = self.timeout


@dataclasses.dataclass
class CodeFragment:
    language: str
    code: str


def detect_and_extract_code(text) -> List[CodeFragment]:
    pattern = r"```(\S+)\n(.*?)```"
    matches = re.findall(pattern, text, re.DOTALL)
    results = []
    for match in matches:
        language, code = match
        fragment = CodeFragment(language, code)
        results.append(fragment)
    return results


def get_username(user: types.User):
    full_name = user.full_name
    username = user.username

    if full_name and username:
        return f'{full_name} (@{username})'
    elif full_name:
        return full_name
    elif username:
        return f'@{username}'
    else:
        raise ValueError("User has no full_name and username")


def message_is_forward(message: types.Message):
    return message.forward_from or message.forward_from_chat or message.forward_sender_name


def get_hide_button():
    keyboard = types.InlineKeyboardMarkup(1)
    keyboard.add(types.InlineKeyboardButton(text='Hide', callback_data='hide'))
    return keyboard


def escape_tg_markdown(text):
    escape_chars = '\*_`\['
    return ''.join('\\' + char if char in escape_chars else char for char in text)


async def send_telegram_message(message: types.Message, text: str, parse_mode=None, reply_markup=None):
    if message.reply_to_message is None:
        send_message = message.answer
    else:
        send_message = message.reply

    try:
        return await send_message(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except CantParseEntities:
        # try to send message without parse_mode once
        return await send_message(text, reply_markup=reply_markup)


async def edit_telegram_message(message: types.Message, text: str, message_id, parse_mode=None):
    chat_id = message.chat.id
    try:
        return await message.bot.edit_message_text(text, chat_id, message_id,  parse_mode=parse_mode)
    except CantParseEntities:
        # try to edit message without parse_mode once
        return await message.bot.edit_message_text(text, chat_id, message_id)


async def send_photo(message: types.Message, photo_bytes, caption=None, reply_markup=None):
    if message.reply_to_message is None:
        send_message = message.answer_photo
    else:
        send_message = message.reply_photo

    return await send_message(photo_bytes, caption=caption, reply_markup=reply_markup)


def merge_dicts(dict_1, dict_2):
    """
    This function merge two dicts containing strings using plus operator on each key
    """
    result = dict_1.copy()
    for key, value in dict_2.items():
        if not isinstance(value, str):
            ValueError("dicts must have strings as values")
        if not key in result:
            result[key] = "" if value is not None else None
        if value is not None:
            result[key] += value

    return result


async def get_usage_response_all_users(db, month_date: date = None) -> str:
    completion_usages = await db.get_all_users_completion_usage(month_date)
    whisper_usages = await db.get_all_users_whisper_usage(month_date)
    image_generation_usages = await db.get_all_users_image_generation_usage(month_date)
    tts_usages = await db.get_all_users_tts_usage(month_date)
    result = []
    # TODO: this will work incorrectly if user never used chat completion but used other features
    for name, user_completion_usages in completion_usages.items():
        user_usage_price = 0

        for usage in user_completion_usages:
            user_usage_price += calculate_completion_usage_price(
                usage.prompt_tokens, usage.completion_tokens, usage.model
            )

        for usage in image_generation_usages.get(name, []):
            user_usage_price += calculate_image_generation_usage_price(
                usage['model'], usage['resolution'], usage['usage_count']
            )

        for usage in tts_usages.get(name, []):
            user_usage_price += calculate_tts_usage_price(usage['characters_count'], usage['model'])

        user_whisper_usage = whisper_usages.get(name, 0)
        user_usage_price += calculate_whisper_usage_price(user_whisper_usage)

        result.append((name, user_usage_price))
    result.sort(key=lambda x: x[1], reverse=True)
    total_price = sum([price for _, price in result])
    result = [f'{name}: ${price}' for name, price in result]
    result.append(f'Total: ${total_price}')
    result = '\n'.join(result)
    if month_date is None:
        result = f'API usage for current month:\n{result}'
    else:
        result = f'API usage for month {month_date.month:02d}/{month_date.year}:\n{result}'
    return result


def generate_document_id(chat_id, message_id):
    return f'{chat_id}_{message_id}'
