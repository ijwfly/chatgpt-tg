import json
import logging
import os
import asyncio
import tempfile
from typing import List

from aiogram import types
from pydub import AudioSegment

import settings
from app.bot.message_processor import MessageProcessor
from app.bot.utils import TypingWorker, message_is_forward, get_username, Timer, generate_document_id
from app.openai_helpers.whisper import get_audio_speech_to_text
from app.storage.db import User, MessageType
from app.storage.user_role import check_access_conditions
from app.storage.vectara import VectaraCorpusClient, VECTARA_SUPPORTED_EXTENSIONS

logger = logging.getLogger(__name__)


class BatchedInputHandler:
    """
    Handles input messages (context and prompt) in batches. If batch has prompt, sends it to OpenAI and sends response
    to user. If batch has no prompt, adds it to context.
    """
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

        # first coroutine for each user handle batching and input processing
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
            try:
                async with self.user_batch_locks[user.id]:
                    while not queue.empty():
                        messages_batch = queue.get_nowait()
                        await self.process_batch(messages_batch, user)
            finally:
                del self.user_batch_queues[user.id]
                del self.user_batch_locks[user.id]

    @staticmethod
    def batch_is_prompt(messages_batch: List[types.Message], user: User):
        """
        Batch is prompt if one message in batch is prompt
        """
        for message in messages_batch:
            if not message_is_forward(message) and not message.voice and not message.document:
                # not voice and not forward and not document - it's a prompt no matter what settings
                return True
            elif message_is_forward(message):
                # if it's a forward, we need to check forward_as_prompt setting
                if user.forward_as_prompt:
                    # forward and forward_as_prompt - it's a prompt
                    return True
                else:
                    # forward and not forward_as_prompt - it's a context, no matter what content it has
                    continue
            elif message.voice and user.voice_as_prompt:
                # voice and voice_as_prompt - it's a prompt
                return True
        # no prompt messages in batch
        return False

    async def process_batch(self, messages_batch: List[types.Message], user: User):
        """
        Processes batch of messages. If batch has prompt, sends it to OpenAI and sends response to user.
        """
        try:
            messages_batch = sorted(messages_batch, key=lambda m: m.message_id)
            first_message = messages_batch[0]
            message_processor = MessageProcessor(self.db, user, first_message)
            for message in messages_batch:
                if message.audio:
                    await self.handle_voice(message, user, message_processor)
                elif message.voice:
                    await self.handle_voice(message, user, message_processor)
                elif message.document:
                    await self.handle_document(message, user, message_processor)
                else:
                    await self.handle_message(message, user, message_processor)

            if not self.batch_is_prompt(messages_batch, user):
                return

            async with TypingWorker(self.bot, first_message.chat.id).typing_context():
                await self.answer_message(message, user, message_processor)
        except Exception as e:
            await message.answer(f'Something went wrong:\n{str(type(e))}\n{e}')
            raise

    async def handle_document(self, message: types.Message, user: User, message_processor: MessageProcessor):
        if not settings.VECTARA_RAG_ENABLED:
            await message.reply('Documents are not supported')
            return
        if not check_access_conditions(settings.USER_ROLE_RAG, user.role):
            await message.reply('You do not have access to this feature')
            return

        _, file_extension = os.path.splitext(message.document.file_name)
        if file_extension[1:] not in VECTARA_SUPPORTED_EXTENSIONS:
            await message.reply(f'Skipping unsupported document format: {file_extension}\n'
                                f'Supported formats: {", ".join(VECTARA_SUPPORTED_EXTENSIONS)}')
            return

        file = await self.bot.get_file(message.document.file_id)
        if file.file_size > 25 * 1024 * 1024:
            await message.reply('Document file is too big')
            return

        async with TypingWorker(self.bot, message.chat.id, TypingWorker.ACTION_UPLOAD_DOCUMENT).typing_context():
            with tempfile.TemporaryDirectory() as temp_dir:
                document_id = generate_document_id(message.chat.id, message.message_id)

                temp_filepath = os.path.join(temp_dir, f'doc_{document_id}_{message.document.file_name}')
                await self.bot.download_file(file.file_path, destination=temp_filepath)
                vectara_client = VectaraCorpusClient(settings.VECTARA_API_KEY, settings.VECTARA_CUSTOMER_ID,
                                                     settings.VECTARA_CORPUS_ID)

                with open(temp_filepath, 'rb') as f:
                    document_info = {
                        "document_id": document_id,
                        "document_name": message.document.file_name,
                    }
                    await vectara_client.upload_document(f, doc_metadata={'document_id': document_id})
                    await message_processor.add_text_as_context(json.dumps(document_info), message.message_id, MessageType.DOCUMENT)

    async def handle_voice(self, message: types.Message, user: User, message_processor: MessageProcessor):
        """
        Handles voice message or audio file with voice. Downloads voice file, converts it to mp3, sends it to whisper,
        sends response to user, adds response to context.
        """
        if message.voice:
            audio_file = message.voice
        elif message.audio:
            audio_file = message.audio
        else:
            raise ValueError('Message has no voice or audio')

        file_id = audio_file.file_id
        file = await self.bot.get_file(file_id)
        if file.file_size > 25 * 1024 * 1024:
            await message.reply('Voice file is too big')
            return

        async with TypingWorker(self.bot, message.chat.id).typing_context():
            with tempfile.TemporaryDirectory() as temp_dir:
                voice_filepath = os.path.join(temp_dir, f'voice_{file_id}')
                mp3_filename = os.path.join(temp_dir, f'voice_{file_id}.mp3')
                await self.bot.download_file(file.file_path, destination=voice_filepath)
                audio = AudioSegment.from_file(voice_filepath)
                audio_length_seconds = len(audio) // 1000 + 1
                await self.db.create_whisper_usage(user.id, audio_length_seconds)
                audio.export(mp3_filename, format="mp3")
                speech_text = await get_audio_speech_to_text(mp3_filename)
                speech_text = f'speech2text:\n{speech_text}'

        # split speech_text to chunks of 4080 symbols
        chunk_size = 4080
        speech_text_chunks = [speech_text[i:i + chunk_size] for i in range(0, len(speech_text), chunk_size)]
        for chunk in speech_text_chunks:
            response = await message.reply(chunk)
            await message_processor.add_text_as_context(chunk, response.message_id)

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
        # TODO: fix memory leak (if message not cancelelled, the token is not deleted)
        is_cancelled = self.cancellation_manager.get_token(user.telegram_id)
        await message_processor.process(is_cancelled)
