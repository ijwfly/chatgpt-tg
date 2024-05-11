import os
import datetime
import tempfile

from aiogram.utils.exceptions import BadRequest
from dateutil.relativedelta import relativedelta

import settings
from app.bot.batched_input_handler import BatchedInputHandler
from app.bot.cancellation_manager import CancellationManager
from app.bot.models_menu import ModelsMenu
from app.bot.scheduled_tasks import build_monthly_usage_task
from app.bot.settings_menu import Settings
from app.bot.user_middleware import UserMiddleware
from app.bot.user_role_manager import UserRoleManager
from app.bot.utils import (get_hide_button, get_usage_response_all_users, TypingWorker)
from app.bot.utils import send_telegram_message
from app.openai_helpers.utils import (calculate_completion_usage_price, calculate_whisper_usage_price, OpenAIAsync,
                                      calculate_image_generation_usage_price, calculate_tts_usage_price)
from app.storage.db import DBFactory, User
from app.storage.user_role import check_access_conditions, UserRole

from aiogram import types, Bot, Dispatcher
from aiogram.utils import executor


class TelegramBot:
    def __init__(self, bot: Bot, dispatcher: Dispatcher):
        self.db = None
        self.bot = bot
        self.dispatcher = dispatcher
        self.dispatcher.register_message_handler(self.open_settings, commands=['settings'])
        self.dispatcher.register_message_handler(self.open_models, commands=['models'])
        self.dispatcher.register_message_handler(self.get_usage, commands=['usage'])
        self.dispatcher.register_message_handler(self.get_usage_all_users, commands=['usage_all'])
        self.dispatcher.register_message_handler(self.reset_dialog, commands=['reset'])
        self.dispatcher.register_message_handler(self.generate_speech, commands=['text2speech'])
        self.dispatcher.register_callback_query_handler(self.process_hide_callback, lambda c: c.data == 'hide')

        # initialized in on_startup
        self.settings = None
        self.models_menu = None
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
        self.models_menu = ModelsMenu(self.bot, self.dispatcher, self.db)
        self.cancellation_manager = CancellationManager(self.bot, self.dispatcher)
        self.role_manager = UserRoleManager(self.bot, self.dispatcher, self.db)
        self.dispatcher.middleware.setup(UserMiddleware(self.db))

        self.monthly_usage_task = build_monthly_usage_task(self.bot, self.db)
        self.monthly_usage_task.start()

        self.batched_handler = BatchedInputHandler(self.bot, self.db, self.cancellation_manager)
        self.dispatcher.register_message_handler(self.batched_handler.handle, content_types=[
            types.ContentType.TEXT, types.ContentType.VIDEO, types.ContentType.PHOTO, types.ContentType.VOICE,
            types.ContentType.DOCUMENT, types.ContentType.AUDIO,
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

    async def get_usage(self, message: types.Message, user: User):
        await self.bot.delete_message(message.chat.id, message.message_id)

        total = 0
        result = []

        completion_usages = await self.db.get_user_current_month_completion_usage(user.id)
        for usage in completion_usages:
            price = calculate_completion_usage_price(usage.prompt_tokens, usage.completion_tokens, usage.model)
            total += price
            result.append(f'*{usage.model}:* {usage.prompt_tokens} prompt, {usage.completion_tokens} completion, ${price}')

        whisper_usage = await self.db.get_user_current_month_whisper_usage(user.id)
        whisper_price = calculate_whisper_usage_price(whisper_usage)
        total += whisper_price
        if whisper_price:
            result.append(f'*Speech2Text:* {whisper_usage} seconds, ${whisper_price}')

        image_generation_usage = await self.db.get_user_current_month_image_generation_usage(user.id)
        for usage in image_generation_usage:
            price = calculate_image_generation_usage_price(
                usage['model'], usage['resolution'], usage['usage_count']
            )
            total += price
            result.append(f'*{usage["model"]}:* {usage["usage_count"]} images, {usage["resolution"]} resolution, ${price}')

        tts_usages = await self.db.get_user_current_month_tts_usage(user.id)
        for tts_usage in tts_usages:
            price = calculate_tts_usage_price(tts_usage['characters_count'], tts_usage['model'])
            total += price
            result.append(f'*{tts_usage["model"]}:* {tts_usage["characters_count"]} characters, ${price}')

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

        result = await get_usage_response_all_users(self.db, month)
        await send_telegram_message(
            message, result, reply_markup=get_hide_button()
        )

    async def open_settings(self, message: types.Message, user: User):
        await self.bot.delete_message(
            chat_id=message.from_user.id,
            message_id=message.message_id,
        )
        await self.settings.send_settings(message, user)

    async def open_models(self, message: types.Message, user: User):
        await self.bot.delete_message(
            chat_id=message.from_user.id,
            message_id=message.message_id,
        )
        await self.models_menu.send_menu(message, user)

    async def generate_speech(self, message: types.Message, user: User):
        # TODO: add reply handling
        # TODO: add usage calculation
        if not check_access_conditions(settings.USER_ROLE_TTS, user.role):
            return

        chat_id = message.chat.id

        if message.reply_to_message:
            db_message = await self.db.get_telegram_message(chat_id, message.reply_to_message.message_id)
        else:
            db_message = await self.db.get_last_message(user.id, chat_id)

        if not db_message:
            await message.answer('No text to generate speech')
            return

        text = db_message.message.get_text_content()
        async with TypingWorker(self.bot, message.chat.id, TypingWorker.ACTION_RECORD_VOICE).typing_context():
            # TODO: decide if tts-1-hd is needed in user settings
            model = 'tts-1'
            response = await OpenAIAsync.instance().audio.speech.create(
                model=model,
                voice=user.tts_voice,
                input=text,
            )
            await self.db.create_tts_usage(user.id, model, len(text))

            # TODO: refactor without saving to file
            with tempfile.TemporaryDirectory() as temp_dir:
                mp3_filename = os.path.join(temp_dir, f'voice_{message.message_id}.mp3')
                await response.astream_to_file(mp3_filename)
                try:
                    await message.answer_voice(open(mp3_filename, 'rb'))
                except BadRequest as e:
                    error_message = f'Error: {e}\nYou should probably try to enable voice messages in your ' \
                                    f'Telegram privacy settings'
                    await message.answer(error_message)
