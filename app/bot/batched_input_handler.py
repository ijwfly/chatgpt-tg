import json
import logging
import os
import asyncio
import re
import tempfile
from contextlib import suppress
from typing import List, Optional

from aiogram import types
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import MessageOriginChannel, MessageOriginChat, MessageOriginHiddenUser, MessageOriginUser
from pydub import AudioSegment

import settings
from app.bot.message_processor import MessageProcessor
from app.bot.utils import TypingWorker, message_is_forward, get_username, Timer
from app.llm_models import get_model_by_name
from app.openai_helpers.utils import calculate_whisper_usage_price
from app.openai_helpers.whisper import get_audio_speech_to_text
from app.runtime.user_input import UserInput, TextInput, ImageInput, VoiceTranscription, \
    SandboxFileInput
from app.sandbox.client import SandboxClient, SandboxError
from app.storage.db import User

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
            if not message_is_forward(message) and not message.voice and not message.video_note and not message.document:
                # not voice and not video_note and not forward and not document - it's a prompt no matter what settings
                return True
            elif message_is_forward(message):
                # if it's a forward, we need to check forward_as_prompt setting
                if user.forward_as_prompt:
                    # forward and forward_as_prompt - it's a prompt
                    return True
                else:
                    # forward and not forward_as_prompt - it's a context, no matter what content it has
                    continue
            elif (message.voice or message.video_note) and user.voice_as_prompt:
                # voice or video note (round video) and voice_as_prompt - it's a prompt
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
            user_input = UserInput()

            for message in messages_batch:
                if message.audio:
                    await self.handle_voice(message, user, user_input)
                elif message.voice:
                    await self.handle_voice(message, user, user_input)
                elif message.video_note:
                    await self.handle_voice(message, user, user_input)
                elif message.document:
                    await self.handle_document(message, user, user_input)
                elif message.photo:
                    # handling image just like message but with some additional checks
                    llm_model = get_model_by_name(user.current_model)
                    if llm_model.capabilities.image_processing:
                        self.handle_message(message, user, user_input)
                    else:
                        # TODO: exception is a bad way to handle this, need to find a better way
                        raise ValueError(f'Image processing is not supported by {llm_model.model_name} model.')
                else:
                    self.handle_message(message, user, user_input)

            # force_prompt: transport captured user-authored content (e.g. a document caption) that
            # needs an answer even though batch_is_prompt() does not consider the batch a prompt
            is_prompt = self.batch_is_prompt(messages_batch, user) \
                or (user_input.force_prompt and user_input.has_content)
            if not is_prompt:
                # Context-only batch: add to context without calling LLM
                message_processor = MessageProcessor(self.db, user, first_message)
                await message_processor.add_context_only(user_input)
                return

            async with TypingWorker(self.bot, first_message.chat.id).typing_context():
                await self.answer_message(first_message, user, user_input)
        except Exception as e:
            logger.exception(f"An error occurred while processing input: %s", e)
            await messages_batch[-1].answer(f'Something went wrong:\n{str(type(e))}\n{e}')
            raise

    async def handle_document(self, message: types.Message, user: User, user_input: UserInput):
        if user.agent_mode and settings.ENABLE_BASH_SANDBOX:
            await self.handle_document_sandbox(message, user, user_input)
            return

        await message.reply('Documents are not supported')

    async def handle_document_sandbox(self, message: types.Message, user: User, user_input: UserInput):
        """Saves an incoming document to the user's bash sandbox workspace (agent mode)."""
        caption = (message.caption or '').strip() or None
        # a caption on a forwarded document is the source text, not a question to the bot
        caption_is_prompt = caption is not None \
            and not (message_is_forward(message) and not user.forward_as_prompt)

        file = await self.bot.get_file(message.document.file_id)
        max_size = settings.SANDBOX_UPLOAD_MAX_MB * 1024 * 1024
        if file.file_size > max_size:
            await message.reply(f'Document file is too big (max {settings.SANDBOX_UPLOAD_MAX_MB} MB)')
            self._add_failed_upload_to_context(
                message, user_input, caption, caption_is_prompt,
                f'file is too big, max {settings.SANDBOX_UPLOAD_MAX_MB} MB',
            )
            return

        sandbox_client = SandboxClient()
        try:
            async with TypingWorker(self.bot, message.chat.id, TypingWorker.ACTION_UPLOAD_DOCUMENT).typing_context():
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_filepath = os.path.join(temp_dir, 'document')
                    await self.bot.download_file(file.file_path, destination=temp_filepath)

                    safe_name = self._sanitize_workspace_filename(message.document.file_name)
                    safe_name = await self._unique_workspace_name(sandbox_client, user.telegram_id, safe_name)

                    with open(temp_filepath, 'rb') as f:
                        data = f.read()
                    result = await sandbox_client.upload_file(user.telegram_id, safe_name, data)

            sandbox_file = SandboxFileInput(
                filename=safe_name,
                size=result.get('size', len(data)),
                tg_message_id=message.message_id,
                caption=caption,
            )
            # a reply to the confirmation must lead to the same dialog branch as the document itself
            with suppress(TelegramBadRequest):
                response = await message.reply(f'Saved to agent workspace: {safe_name}')
                sandbox_file.alias_tg_message_ids.append(response.message_id)
            user_input.sandbox_files.append(sandbox_file)
            if caption_is_prompt:
                user_input.force_prompt = True
        except SandboxError as e:
            logger.error(f'Failed to save document to sandbox: {e}')
            await message.reply(f'Failed to save document to agent workspace: {e}')
            self._add_failed_upload_to_context(message, user_input, caption, caption_is_prompt, str(e))

    @staticmethod
    def _add_failed_upload_to_context(message: types.Message, user_input: UserInput, caption: Optional[str],
                                      caption_is_prompt: bool, reason: str):
        """
        The file did not make it to the workspace. If the user wrote something along with it, their text
        still has to reach the context (and get an answer) — otherwise the question is silently dropped.
        """
        if not caption:
            return

        file_name = message.document.file_name if message.document else 'file'
        user_input.text_inputs.append(TextInput(
            text=f'[failed to upload file to agent workspace] {file_name} ({reason})\n{caption}',
            tg_message_id=message.message_id,
        ))
        if caption_is_prompt:
            user_input.force_prompt = True

    @staticmethod
    def _sanitize_workspace_filename(filename: str) -> str:
        safe_name = os.path.basename(filename or '')
        # \w is unicode-aware: keeps letters in any alphabet (incl. cyrillic) and digits
        safe_name = re.sub(r'[^\w.-]', '_', safe_name)
        if not safe_name.strip('._'):
            safe_name = 'file'
        return safe_name

    @staticmethod
    async def _unique_workspace_name(sandbox_client: SandboxClient, telegram_user_id: int, filename: str) -> str:
        base, ext = os.path.splitext(filename)
        candidate = filename
        counter = 1
        while (await sandbox_client.stat(telegram_user_id, candidate)).get('type') != 'missing':
            candidate = f'{base}_{counter}{ext}'
            counter += 1
        return candidate

    async def handle_voice(self, message: types.Message, user: User, user_input: UserInput):
        """
        Handles voice message, audio file, or video note (round video). Downloads the file, extracts audio and
        converts it to mp3 (ffmpeg handles video notes too), sends it to whisper, sends response to user,
        adds response to context.
        """
        if message.voice:
            audio_file = message.voice
        elif message.audio:
            audio_file = message.audio
        elif message.video_note:
            audio_file = message.video_note
        else:
            raise ValueError('Message has no voice, audio or video note')

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
                price = calculate_whisper_usage_price(audio_length_seconds)
                await self.db.create_whisper_usage(user.id, audio_length_seconds, price)
                audio.export(mp3_filename, format="mp3")
                speech_text = await get_audio_speech_to_text(mp3_filename)
                speech_text = f'speech2text:\n{speech_text}'

        # split speech_text to chunks of 4080 symbols
        chunk_size = 4080
        speech_text_chunks = [speech_text[i:i + chunk_size] for i in range(0, len(speech_text), chunk_size)]
        transcriptions = []
        for chunk in speech_text_chunks:
            tg_message_id = -1
            with suppress(TelegramBadRequest):
                response = await message.reply(chunk)
                tg_message_id = response.message_id
            transcriptions.append(VoiceTranscription(
                text=chunk,
                tg_message_id=tg_message_id,
            ))

        if transcriptions:
            # a reply to the user's own voice message must lead to the whole transcription: the last chunk
            # is the only message whose branch (previous_message_ids + itself) contains all the chunks
            transcriptions[-1].alias_tg_message_ids.append(message.message_id)
            user_input.voice_transcriptions.extend(transcriptions)

    @staticmethod
    def handle_message(message: types.Message, user: User, user_input: UserInput):
        """
        Handles text message. If message is forward, adds it to context with additional info. If message is not forward,
        adds it to context.
        """
        # aiogram 3 models are frozen: a caption is used as the text without mutating the message
        text = message.text if message.text else message.caption

        if text is None and message.photo is None:
            return

        if message_is_forward(message) and not user.forward_as_prompt:
            BatchedInputHandler._handle_forwarded_message(message, user, user_input, text)
            return

        if message.photo:
            # largest photo
            photo = message.photo[-1]
            user_input.text_inputs.append(TextInput(
                text=text,
                tg_message_id=message.message_id,
                images=[ImageInput(
                    file_id=photo.file_id,
                    width=photo.width,
                    height=photo.height,
                )],
            ))
        elif text:
            user_input.text_inputs.append(TextInput(
                text=text,
                tg_message_id=message.message_id,
            ))

    @staticmethod
    def _forward_author(message: types.Message) -> Optional[str]:
        """Human-readable author of a forwarded message (Bot API 7 forward_origin with legacy-field fallback)."""
        origin = message.forward_origin
        if isinstance(origin, MessageOriginUser):
            return get_username(origin.sender_user)
        if isinstance(origin, MessageOriginHiddenUser):
            return origin.sender_user_name
        if isinstance(origin, (MessageOriginChat, MessageOriginChannel)):
            chat = origin.sender_chat if isinstance(origin, MessageOriginChat) else origin.chat
            return f'Chat name "{chat.full_name or chat.title}"'
        if message.forward_from:
            return get_username(message.forward_from)
        if message.forward_sender_name:
            return message.forward_sender_name
        if message.forward_from_chat:
            return f'Chat name "{message.forward_from_chat.full_name or message.forward_from_chat.title}"'
        return None

    @staticmethod
    def _handle_forwarded_message(message: types.Message, user: User, user_input: UserInput, text: Optional[str]):
        """
        Handles forwarded message. Adds it to context with additional info.
        """
        username = BatchedInputHandler._forward_author(message)
        if text:
            forwarded_text = f'{username}:\n{text}' if username else text
        else:
            forwarded_text = f'{username}:' if username else None

        images = None
        if message.photo:
            # largest photo
            photo = message.photo[-1]
            images = [ImageInput(
                file_id=photo.file_id,
                width=photo.width,
                height=photo.height,
            )]

        user_input.text_inputs.append(TextInput(
            text=forwarded_text,
            tg_message_id=message.message_id,
            images=images,
        ))

    async def answer_message(self, first_message: types.Message, user: User, user_input: UserInput):
        """
        Sends prompt to OpenAI, sends response to user, adds response to context.
        """
        # TODO: fix memory leak (if message not cancelelled, the token is not deleted)
        is_cancelled = self.cancellation_manager.get_token(user.telegram_id)
        message_processor = MessageProcessor(self.db, user, first_message)
        await message_processor.process(is_cancelled, user_input)
