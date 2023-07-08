import os.path
import tempfile

import settings
from app.bot.dialog_manager import DialogManager
from app.bot.settings_menu import Settings
from app.bot.utils import TypingWorker, detect_and_extract_code
from app.openai_helpers.whisper import get_audio_speech_to_text
from app.storage.db import DBFactory
from app.openai_helpers.chatgpt import ChatGPT, GptModel, DialogMessage

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
        self.dispatcher.register_message_handler(self.set_current_model, commands=['gpt3', 'gpt4'])
        self.dispatcher.register_message_handler(self.handler)

        # initialized in on_startup
        self.settings = None

    async def on_startup(self, _):
        self.db = await DBFactory().create_database(
            settings.POSTGRES_USER, settings.POSTGRES_PASSWORD,
            settings.POSTGRES_HOST, settings.POSTGRES_PORT, settings.POSTGRES_DATABASE
        )
        self.settings = Settings(self.bot, self.dispatcher, self.db)

    async def on_shutdown(self, _):
        await DBFactory().close_database()
        self.db = None

    async def handler(self, message: types.Message):
        try:
            if message.text is None:
                return
            await self.simple_answer(message)
        except Exception as e:
            await message.answer(f'Something went wrong:\n{str(type(e))}\n{e}')
            raise

    def run(self):
        executor.start_polling(self.dispatcher, on_startup=self.on_startup, on_shutdown=self.on_shutdown)

    async def handle_voice(self, message: types.Message):
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
                audio.export(mp3_filename, format="mp3")
                speech_text = await get_audio_speech_to_text(mp3_filename)
                speech_text = f'speech2text:\n{speech_text}'

        dialog_manager = DialogManager(self.db)
        await dialog_manager.process_dialog(message)
        speech_dialog_message = DialogMessage(role="user", content=speech_text)
        response = await message.reply(speech_text)
        await dialog_manager.add_message_to_dialog(speech_dialog_message, response.message_id)

    async def simple_answer(self, message: types.Message):
        dialog_manager = DialogManager(self.db)

        context_dialog_messages = await dialog_manager.process_dialog(message)
        input_dialog_message = await dialog_manager.prepare_input_message(message)
        user = dialog_manager.get_user()

        chat_gpt = ChatGPT(user.current_model, user.gpt_mode)

        async with TypingWorker(message.bot, message.from_user.id).typing_context():
            response_dialog_message = await chat_gpt.send_user_message(input_dialog_message, context_dialog_messages)

        code_fragments = detect_and_extract_code(response_dialog_message.content)
        parse_mode = types.ParseMode.MARKDOWN if code_fragments else types.ParseMode.HTML
        if message.reply_to_message is None:
            response = await message.answer(response_dialog_message.content, parse_mode=parse_mode)
        else:
            response = await message.reply(response_dialog_message.content, parse_mode=parse_mode)

        await dialog_manager.add_message_to_dialog(input_dialog_message, message.message_id)
        await dialog_manager.add_message_to_dialog(response_dialog_message, response.message_id)

    async def reset_dialog(self, message: types.Message):
        user = await self.db.get_or_create_user(message.from_user.id)

        await self.db.deactivate_active_dialog(user.id)
        await message.answer('👌')

    async def set_current_model(self, message: types.Message):
        model = GptModel.GPT_35_TURBO if message.get_command() == '/gpt3' else GptModel.GPT_4
        user = await self.db.get_or_create_user(message.from_user.id)
        user.current_model = model
        await self.db.update_user(user)
        await message.answer('👌')

    async def set_current_mode(self, message: types.Message, gpt_mode):
        user = await self.db.get_or_create_user(message.from_user.id)
        user.gpt_mode = gpt_mode
        await self.db.update_user(user)
        await message.answer('👌')

    async def open_settings(self, message: types.Message):
        await self.bot.delete_message(
            chat_id=message.from_user.id,
            message_id=message.message_id
        )
        await self.settings.send_settings(message)
