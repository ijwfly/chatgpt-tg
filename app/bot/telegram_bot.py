import datetime
import os.path
import tempfile

from dateutil.relativedelta import relativedelta

import settings
from app.bot.cancellation_manager import CancellationManager
from app.bot.message_processor import MessageProcessor
from app.bot.scheduled_tasks import build_monthly_usage_task
from app.bot.settings_menu import Settings
from app.bot.user_middleware import UserMiddleware
from app.bot.user_role_manager import UserRoleManager
from app.bot.utils import (TypingWorker, get_username, message_is_forward, get_hide_button,
                           get_completion_usage_response_all_users)
from app.bot.utils import send_telegram_message
from app.openai_helpers.utils import calculate_completion_usage_price, calculate_whisper_usage_price
from app.openai_helpers.whisper import get_audio_speech_to_text
from app.storage.db import DBFactory, User
from app.storage.user_role import check_access_conditions, UserRole
from app.openai_helpers.chatgpt import GptModel

from aiogram import types, Bot, Dispatcher
from aiogram.utils import executor
from pydub import AudioSegment


class TelegramBot:
    def __init__(self, bot: Bot, dispatcher: Dispatcher):
        self.db = None
        self.bot = bot
        self.dispatcher = dispatcher
        self.dispatcher.register_message_handler(self.handle_voice, content_types=types.ContentType.VOICE)
        self.dispatcher.register_message_handler(self.reset_dialog, commands=['reset'])
        self.dispatcher.register_message_handler(self.open_settings, commands=['settings'])
        self.dispatcher.register_message_handler(self.set_current_model, commands=['gpt3', 'gpt4', 'gpt4turbo', 'gpt4vision'])
        self.dispatcher.register_message_handler(self.get_usage, commands=['usage'])
        self.dispatcher.register_message_handler(self.get_usage_all_users, commands=['usage_all'])
        self.dispatcher.register_message_handler(
            self.handler, content_types=[types.ContentType.TEXT, types.ContentType.VIDEO, types.ContentType.PHOTO]
        )
        self.dispatcher.register_callback_query_handler(self.process_hide_callback, lambda c: c.data == 'hide')

        # initialized in on_startup
        self.settings = None
        self.cancellation_manager = None
        self.role_manager = None
        self.monthly_usage_task = None

    async def on_startup(self, _):
        self.db = await DBFactory.create_database(
            settings.POSTGRES_USER, settings.POSTGRES_PASSWORD,
            settings.POSTGRES_HOST, settings.POSTGRES_PORT, settings.POSTGRES_DATABASE
        )
        self.settings = Settings(self.bot, self.dispatcher, self.db)
        self.cancellation_manager = CancellationManager(self.bot, self.dispatcher)
        self.role_manager = UserRoleManager(self.bot, self.dispatcher, self.db)
        self.dispatcher.middleware.setup(UserMiddleware(self.db))

        self.monthly_usage_task = build_monthly_usage_task(self.bot, self.db)
        self.monthly_usage_task.start()

        # all commands are added to global scope by default, except for admin commands
        commands = self.role_manager.get_role_commands(UserRole.ADVANCED)
        await self.bot.set_my_commands(commands)

    async def on_shutdown(self, _):
        if self.monthly_usage_task:
            await self.monthly_usage_task.stop()
        await DBFactory().close_database()
        self.db = None

    def run(self):
        executor.start_polling(self.dispatcher, on_startup=self.on_startup, on_shutdown=self.on_shutdown)

    async def process_hide_callback(self, callback_query: types.CallbackQuery):
        await self.bot.delete_message(
            chat_id=callback_query.from_user.id,
            message_id=callback_query.message.message_id
        )
        await self.bot.answer_callback_query(callback_query.id)

    async def handler(self, message: types.Message, user: User):
        if message.caption and not message.text:
            message.text = message.caption

        if message.text is None and message.photo is None:
            return

        if message.media_group_id is not None:
            # HACK: add media group as context to process them at once
            await MessageProcessor(self.db, user, message).add_message_as_context()

        if message_is_forward(message) and not user.forward_as_prompt:
            await self.handle_forwarded_message(message, user)
            return

        try:
            async with TypingWorker(self.bot, message.chat.id).typing_context():
                await self.answer_message(message, user)
        except Exception as e:
            await message.answer(f'Something went wrong:\n{str(type(e))}\n{e}')
            raise

    async def handle_forwarded_message(self, message: types.Message, user: User):
        # add forwarded text as context to current dialog, not as prompt
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
        # HACK: hack with aiogram.Message to process forwarded message as context
        message.text = forwarded_text

        await MessageProcessor(self.db, user, message).add_message_as_context()

    async def handle_voice(self, message: types.Message, user: User):
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

        if user.voice_as_prompt:
            # HACK: hack with aiogram.Message to process voice as text prompt
            message.text = speech_text
            message.message_id = response.message_id
            await self.handler(message, user)
        else:
            # add voice message text as context to current dialog, not as prompt
            message_processor = MessageProcessor(self.db, user, message)
            await message_processor.add_text_as_context(speech_text, response.message_id)

    async def answer_message(self, message: types.Message, user: User):
        message_processor = MessageProcessor(self.db, user, message)
        is_cancelled = self.cancellation_manager.get_token(message.from_user.id)
        await message_processor.process_message(is_cancelled)

    async def reset_dialog(self, message: types.Message, user: User):
        await self.db.create_reset_message(user.id, message.chat.id)
        await message.answer('👌')

    async def set_current_model(self, message: types.Message, user: User):
        if not check_access_conditions(settings.USER_ROLE_CHOOSE_MODEL, user.role):
            await message.answer(f'Your model is {user.current_model}. You have no permissions to change model')
            return

        command_to_model = {
            'gpt3': GptModel.GPT_35_TURBO,
            'gpt4': GptModel.GPT_4,
            'gpt4turbo': GptModel.GPT_4_TURBO_PREVIEW,
            'gpt4vision': GptModel.GPT_4_VISION_PREVIEW,
        }

        command = message.get_command(pure=True)
        model = command_to_model.get(command)
        if model is None:
            raise ValueError('Unknown model name')
        user.current_model = model
        await self.db.update_user(user)
        await message.answer('👌')

    async def get_usage(self, message: types.Message, user: User):
        await self.bot.delete_message(message.chat.id, message.message_id)
        whisper_usage = await self.db.get_user_current_month_whisper_usage(user.id)
        whisper_price = calculate_whisper_usage_price(whisper_usage)

        completion_usages = await self.db.get_user_current_month_completion_usage(user.id)
        result = []
        total = whisper_price
        for usage in completion_usages:
            price = calculate_completion_usage_price(usage.prompt_tokens, usage.completion_tokens, usage.model)
            total += price
            result.append(f'*{usage.model}:* {usage.prompt_tokens} prompt, {usage.completion_tokens} completion, ${price}')
        if whisper_price:
            result.append(f'*Speech2Text:* {whisper_usage} seconds, ${whisper_price}')
        result.append(f'*Total:* ${total}')
        await send_telegram_message(
            message, '\n'.join(result), types.ParseMode.MARKDOWN, reply_markup=get_hide_button()
        )

    async def get_usage_all_users(self, message: types.Message, user: User):
        if not check_access_conditions(UserRole.ADMIN, user.role):
            return

        await self.bot.delete_message(message.chat.id, message.message_id)

        # parse command args
        args = message.get_args().split(' ')
        month = None
        if len(args) == 1 and args[0].replace('-', '').isdecimal():
            month_offset = int(args[0])
            month = datetime.datetime.now(settings.POSTGRES_TIMEZONE) + relativedelta(months=month_offset)
            month = month.date()

        result = await get_completion_usage_response_all_users(self.db, month)
        await send_telegram_message(
            message, result, reply_markup=get_hide_button()
        )

    async def open_settings(self, message: types.Message, user: User):
        await self.bot.delete_message(
            chat_id=message.from_user.id,
            message_id=message.message_id,
        )
        await self.settings.send_settings(message, user)
