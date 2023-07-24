import os.path
import tempfile

import settings
from app.bot.chatgpt_manager import ChatGptManager
from app.bot.dialog_manager import DialogManager
from app.bot.settings_menu import Settings
from app.bot.user_middleware import UserMiddleware
from app.bot.utils import TypingWorker, detect_and_extract_code, get_username, message_is_forward
from app.openai_helpers.function_storage import FunctionStorage
from app.openai_helpers.utils import calculate_completion_usage_price, calculate_whisper_usage_price
from app.openai_helpers.whisper import get_audio_speech_to_text
from app.storage.db import DBFactory, User
from app.openai_helpers.chatgpt import ChatGPT, GptModel, DialogMessage

from aiogram.utils.exceptions import CantParseEntities
from aiogram import types, Bot, Dispatcher
from aiogram.utils import executor
from pydub import AudioSegment


class TelegramBot:
    def __init__(self, bot: Bot, dispatcher: Dispatcher, function_storage: FunctionStorage = None):
        self.db = None
        self.bot = bot
        self.dispatcher = dispatcher
        self.dispatcher.register_message_handler(self.handle_voice, content_types=types.ContentType.VOICE)
        self.dispatcher.register_message_handler(self.reset_dialog, commands=['reset'])
        self.dispatcher.register_message_handler(self.open_settings, commands=['settings'])
        self.dispatcher.register_message_handler(self.set_current_model, commands=['gpt3', 'gpt4'])
        self.dispatcher.register_message_handler(self.get_usage, commands=['usage'])
        self.dispatcher.register_message_handler(self.handler)

        self.function_storage = function_storage

        # initialized in on_startup
        self.settings = None

    async def on_startup(self, _):
        self.db = await DBFactory().create_database(
            settings.POSTGRES_USER, settings.POSTGRES_PASSWORD,
            settings.POSTGRES_HOST, settings.POSTGRES_PORT, settings.POSTGRES_DATABASE
        )
        self.settings = Settings(self.bot, self.dispatcher, self.db)
        self.dispatcher.middleware.setup(UserMiddleware(self.db))

    async def on_shutdown(self, _):
        await DBFactory().close_database()
        self.db = None

    def run(self):
        executor.start_polling(self.dispatcher, on_startup=self.on_startup, on_shutdown=self.on_shutdown)

    async def handler(self, message: types.Message, user: User):
        if message.text is None:
            return

        if message_is_forward(message):
            await self.handle_forward_text(message, user)
            return

        try:
            await self.answer_text_message(message, user)
        except Exception as e:
            await message.answer(f'Something went wrong:\n{str(type(e))}\n{e}')
            raise

    async def handle_forward_text(self, message: types.Message, user: User):
        if user.forward_as_prompt:
            await self.answer_text_message(message, user)
            return

        # add forwarded text as context to current dialog, not as prompt
        if message.forward_from:
            username = get_username(message.forward_from)
        elif message.forward_sender_name:
            username = message.forward_sender_name
        else:
            username = None
        forwarded_text = f'{username}:\n{message.text}' if username else message.text

        dialog_manager = DialogManager(self.db, user)
        await dialog_manager.process_dialog(message)
        forward_dialog_message = DialogMessage(role="user", content=forwarded_text)
        await dialog_manager.add_message_to_dialog(forward_dialog_message, message.message_id)

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

        dialog_manager = DialogManager(self.db, user)
        await dialog_manager.process_dialog(message)
        speech_dialog_message = DialogMessage(role="user", content=speech_text)
        response = await message.reply(speech_text)

        if user.voice_as_prompt:
            # HACK: hack with aiogram.Message to process voice as text prompt
            message.text = speech_text
            await self.handler(message, user)
        else:
            # add voice message text as context to current dialog, not as prompt
            await dialog_manager.add_message_to_dialog(speech_dialog_message, response.message_id)

    @staticmethod
    async def send_telegram_message(message: types.Message, text: str, parse_mode=None):
        if message.reply_to_message is None:
            send_message = message.answer
        else:
            send_message = message.reply

        try:
            return await send_message(text, parse_mode=parse_mode)
        except CantParseEntities:
            # try to send message without parse_mode once
            return await send_message(text)

    async def answer_text_message(self, message: types.Message, user: User):
        dialog_manager = DialogManager(self.db, user)

        context_dialog_messages = await dialog_manager.process_dialog(message)
        input_dialog_message = dialog_manager.prepare_input_message(message)

        function_storage = self.function_storage if user.use_functions else None
        chat_gpt_manager = ChatGptManager(ChatGPT(user.current_model, user.gpt_mode, function_storage), self.db)

        async with TypingWorker(self.bot, message.from_user.id).typing_context():
            response_dialog_message = await chat_gpt_manager.send_user_message(user, input_dialog_message, context_dialog_messages)

        await self.handle_gpt_response(user, chat_gpt_manager, dialog_manager, message, input_dialog_message, response_dialog_message)

    async def handle_gpt_response(self, user, chat_gpt_manager, dialog_manager, message, input_dialog_message, response_dialog_message):
        if response_dialog_message.function_call:
            function_name = response_dialog_message.function_call.name
            function_args = response_dialog_message.function_call.arguments
            async with TypingWorker(self.bot, message.from_user.id).typing_context():
                function_response_raw = await self.function_storage.run_function(function_name, function_args)

                await dialog_manager.add_message_to_dialog(input_dialog_message, message.message_id)

                function_response = dialog_manager.prepare_function_response(function_name, function_response_raw)
                context_dialog_messages = dialog_manager.get_dialog_messages()

                response_dialog_message = await chat_gpt_manager.send_user_message(user, function_response, context_dialog_messages)
                function_response_text = f'Function call: {function_name}({function_args})\n\n{function_response_raw}'
                function_response_tg_message = await self.send_telegram_message(message, function_response_text)
                await dialog_manager.add_message_to_dialog(function_response, function_response_tg_message.message_id)

                if response_dialog_message.content:
                    response = await self.send_telegram_message(message, response_dialog_message.content)
                    await dialog_manager.add_message_to_dialog(response_dialog_message, response.message_id)
                else:
                    await self.handle_gpt_response(user, chat_gpt_manager, dialog_manager, message, input_dialog_message, response_dialog_message)
        else:
            code_fragments = detect_and_extract_code(response_dialog_message.content)
            parse_mode = types.ParseMode.MARKDOWN if code_fragments else None
            response = await self.send_telegram_message(message, response_dialog_message.content, parse_mode)
            await dialog_manager.add_message_to_dialog(input_dialog_message, message.message_id)
            await dialog_manager.add_message_to_dialog(response_dialog_message, response.message_id)

    async def reset_dialog(self, message: types.Message, user: User):
        await self.db.deactivate_active_dialog(user.id)
        await message.answer('👌')

    async def set_current_model(self, message: types.Message, user: User):
        model = GptModel.GPT_35_TURBO if message.get_command() == '/gpt3' else GptModel.GPT_4
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
            result.append(f'{usage.model}: {usage.prompt_tokens} prompt, {usage.completion_tokens} completion, ${price}')
        if whisper_price:
            result.append(f'Speech2Text: {whisper_usage} seconds, ${whisper_price}')
        result.append(f'Total: ${total}')
        await self.send_telegram_message(message, '\n'.join(result))

    async def open_settings(self, message: types.Message):
        await self.bot.delete_message(
            chat_id=message.from_user.id,
            message_id=message.message_id
        )
        await self.settings.send_settings(message)
