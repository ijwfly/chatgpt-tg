import os
import datetime
import tempfile

from aiogram.utils.exceptions import BadRequest
from dateutil.relativedelta import relativedelta

import settings
from app.bot.batched_input_handler import BatchedInputHandler
from app.bot.cancellation_manager import CancellationManager
from app.bot.scheduled_tasks import build_monthly_usage_task
from app.bot.settings_menu import Settings
from app.bot.user_middleware import UserMiddleware
from app.bot.user_role_manager import UserRoleManager
from app.bot.utils import (get_hide_button, get_completion_usage_response_all_users, TypingWorker)
from app.bot.utils import send_telegram_message
from app.openai_helpers.utils import calculate_completion_usage_price, calculate_whisper_usage_price, OpenAIAsync
from app.storage.db import DBFactory, User
from app.storage.user_role import check_access_conditions, UserRole
from app.openai_helpers.chatgpt import GptModel

from aiogram import types, Bot, Dispatcher
from aiogram.utils import executor


class TelegramBot:
    def __init__(self, bot: Bot, dispatcher: Dispatcher):
        self.db = None
        self.bot = bot
        self.dispatcher = dispatcher
        self.dispatcher.register_message_handler(self.open_settings, commands=['settings'])
        self.dispatcher.register_message_handler(self.get_usage, commands=['usage'])
        self.dispatcher.register_message_handler(self.get_usage_all_users, commands=['usage_all'])
        self.dispatcher.register_message_handler(self.set_current_model, commands=['gpt3', 'gpt4', 'gpt4turbo', 'gpt4vision'])
        self.dispatcher.register_message_handler(self.reset_dialog, commands=['reset'])
        self.dispatcher.register_message_handler(self.generate_speech, commands=['tts'])
        self.dispatcher.register_callback_query_handler(self.process_hide_callback, lambda c: c.data == 'hide')

        # initialized in on_startup
        self.settings = None
        self.cancellation_manager = None
        self.role_manager = None
        self.monthly_usage_task = None
        self.batched_handler = None

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

        self.batched_handler = BatchedInputHandler(self.bot, self.db, self.cancellation_manager)
        self.dispatcher.register_message_handler(self.batched_handler.handle, content_types=[
            types.ContentType.TEXT, types.ContentType.VIDEO, types.ContentType.PHOTO, types.ContentType.VOICE
        ])

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

    async def generate_speech(self, message: types.Message, user: User):
        last_message = await self.db.get_last_message(user.id, message.chat.id)
        text = last_message.message.get_text_content()
        if not text:
            await message.answer('No text to generate speech')
            return
        async with TypingWorker(self.bot, message.chat.id, TypingWorker.ACTION_RECORD_VOICE).typing_context():
            response = await OpenAIAsync.instance().audio.speech.create(
                model='tts-1',
                voice='onyx',
                input=text,
            )
            # TODO: refactor without saving to file
            with tempfile.TemporaryDirectory() as temp_dir:
                mp3_filename = os.path.join(temp_dir, f'voice_{message.message_id}.mp3')
                response.stream_to_file(mp3_filename)
                try:
                    await message.answer_voice(open(mp3_filename, 'rb'))
                except BadRequest as e:
                    error_message = f'Error: {e}\nYou should probably try to enable voice messages in your ' \
                                    f'Telegram privacy settings'
                    await message.answer(error_message)
