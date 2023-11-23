import logging
import os
import asyncio
import tempfile
from typing import List

from aiogram import types
from pydub import AudioSegment

from app.bot.message_processor import MessageProcessor
from app.bot.utils import TypingWorker, message_is_forward, get_username, Timer
from app.openai_helpers.whisper import get_audio_speech_to_text
from app.storage.db import User


logger = logging.getLogger(__name__)


class BatchedHandler:
    def __init__(self, bot, db, cancellation_manager):
        self.bot = bot
        self.db = db
        self.cancellation_manager = cancellation_manager

        self.user_batches = {}
        self.user_locks = {}
        self.user_timers = {}

        self.user_batch_queues = {}
        self.user_batch_locks = {}

    async def handle(self, message: types.Message, user: User):
        """Collects messages in batches and handles them one by one in order they were received"""
        if user.id not in self.user_batches:
            self.user_batches[user.id] = []
            self.user_locks[user.id] = asyncio.Lock()
            self.user_timers[user.id] = Timer()

        async with self.user_locks[user.id]:
            self.user_batches[user.id].append(message)
            self.user_timers[user.id].reset()

        # first coroutine for each user will handle batching process
        if len(self.user_batches[user.id]) == 1:
            await self.user_timers[user.id].sleep()
            async with self.user_locks[user.id]:
                messages_batch = self.user_batches[user.id]
                del self.user_batches[user.id]
                del self.user_timers[user.id]
                del self.user_locks[user.id]
            await self.handle_batch(messages_batch, user)

    async def handle_batch(self, messages_batch: List[types.Message], user: User):
        """Handles batches one by one in order they were received"""
        if user.id not in self.user_batch_queues:
            self.user_batch_queues[user.id] = asyncio.Queue()
            self.user_batch_locks[user.id] = asyncio.Lock()

        queue = self.user_batch_queues[user.id]

        await queue.put(messages_batch)

        # If lock is already acquired, exit
        if not self.user_batch_locks[user.id].locked():
            async with self.user_batch_locks[user.id]:
                while not queue.empty():
                    messages_batch = queue.get_nowait()
                    await self.process_batch(messages_batch, user)

            del self.user_batch_queues[user.id]

    @staticmethod
    def batch_is_prompt(messages_batch: List[types.Message], user: User):
        """
        Batch is prompt if one message in batch is prompt
        """
        for message in messages_batch:
            if not message_is_forward(message) and not message.voice:
                # not voice and not forward - it's a prompt
                return True
            elif message_is_forward(message) and user.forward_as_prompt:
                # forward and forward_as_prompt - it's a prompt
                return True
            elif message.voice and user.voice_as_prompt:
                # voice and voice_as_prompt - it's a prompt
                return True
        # no prompt messages in batch
        return False

    async def process_batch(self, messages_batch: List[types.Message], user: User):
        """
        Processes batch of messages. If batch has prompt, sends it to OpenAI and sends response to user.
        """
        messages_batch = sorted(messages_batch, key=lambda m: m.message_id)
        first_message = messages_batch[0]
        message_processor = MessageProcessor(self.db, user, first_message)
        for message in messages_batch:
            if message.voice:
                await self.handle_voice(message, user, message_processor)
            else:
                await self.handle_message(message, user, message_processor)

        if not self.batch_is_prompt(messages_batch, user):
            return

        try:
            async with TypingWorker(self.bot, first_message.chat.id).typing_context():
                await self.answer_message(message, user, message_processor)
        except Exception as e:
            await message.answer(f'Something went wrong:\n{str(type(e))}\n{e}')
            raise

    async def handle_voice(self, message: types.Message, user: User, message_processor: MessageProcessor):
        """
        Handles voice message. Downloads voice file, converts it to mp3, sends it to whisper, sends response to user,
        adds response to context.
        """
        file = await self.bot.get_file(message.voice.file_id)
        if file.file_size > 25 * 1024 * 1024:
            await message.reply('Voice file is too big')
            return

        async with TypingWorker(self.bot, message.chat.id).typing_context():
            with tempfile.TemporaryDirectory() as temp_dir:
                ogg_filepath = os.path.join(temp_dir, f'voice_{message.voice.file_id}.ogg')
                mp3_filename = os.path.join(temp_dir, f'voice_{message.voice.file_id}.mp3')
                await self.bot.download_file(file.file_path, destination=ogg_filepath)
                audio = AudioSegment.from_ogg(ogg_filepath)
                audio_length_seconds = len(audio) // 1000 + 1
                await self.db.create_whisper_usage(user.id, audio_length_seconds)
                audio.export(mp3_filename, format="mp3")
                speech_text = await get_audio_speech_to_text(mp3_filename)
                speech_text = f'speech2text:\n{speech_text}'

        response = await message.reply(speech_text)
        await message_processor.add_text_as_context(speech_text, response.message_id)

    async def handle_message(self, message: types.Message, user: User, message_processor: MessageProcessor):
        """
        Handles text message. If message is forward, adds it to context with additional info. If message is not forward,
        adds it to context.
        """
        if message.caption and not message.text:
            message.text = message.caption

        if message.text is None and message.photo is None:
            return

        if message_is_forward(message) and not user.forward_as_prompt:
            await self.handle_forwarded_message(message, user, message_processor)
            return

        await message_processor.add_message_as_context(message=message)

    async def handle_forwarded_message(self, message: types.Message, user: User, message_processor: MessageProcessor):
        """
        Handles forwarded message. Adds it to context with additional info.
        """
        if message.forward_from:
            username = get_username(message.forward_from)
        elif message.forward_sender_name:
            username = message.forward_sender_name
        elif message.forward_from_chat:
            username = message.forward_from_chat.full_name or message.forward_from_chat.title
            username = f'Chat name "{username}"'
        else:
            username = None
        forwarded_text = f'{username}:\n{message.text}' if username else message.text
        message.text = forwarded_text

        await message_processor.add_message_as_context(message=message)

    async def answer_message(self, message: types.Message, user: User, message_processor: MessageProcessor):
        """
        Sends prompt to OpenAI, sends response to user, adds response to context.
        """
        is_cancelled = self.cancellation_manager.get_token(user.telegram_id)
        await message_processor.process(is_cancelled)
