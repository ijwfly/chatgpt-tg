from app import settings
from app.bot.dialog_manager import DialogManager
from app.bot.utils import TypingWorker

from app.openai_helpers.chatgpt import ChatGPT, GptModel

from aiogram import types


class TelegramBot:
    def __init__(self, db):
        self.db = db

    async def simple_answer(self, message: types.Message):
        dialog_manager = DialogManager(self.db)

        context_dialog_messages = await dialog_manager.process_dialog(message)
        input_dialog_message = await dialog_manager.prepare_input_message(message)
        user = dialog_manager.get_user()

        chat_gpt = ChatGPT(user.current_model, user.gpt_mode)

        async with TypingWorker(message.bot, message.from_user.id).typing_context():
            response_dialog_message = await chat_gpt.send_user_message(input_dialog_message, context_dialog_messages)

        if message.reply_to_message is None:
            response = await message.answer(response_dialog_message.content)
        else:
            response = await message.reply(response_dialog_message.content)

        await dialog_manager.add_message_to_dialog(input_dialog_message, message.message_id)
        await dialog_manager.add_message_to_dialog(response_dialog_message, response.message_id)

    async def reset_dialog(self, message: types.Message):
        user = await self.db.get_user(message.from_user.id)
        if user is None:
            user = await self.db.create_user(message.from_user.id)

        await self.db.deactivate_active_dialog(user.id)
        await message.answer('👌')

    async def set_current_model(self, message: types.Message, model):
        user = await self.db.get_user(message.from_user.id)
        if user is None:
            user = await self.db.create_user(message.from_user.id)
        user.current_model = model
        await self.db.update_user(user)
        await message.answer('👌')

    async def set_current_mode(self, message: types.Message, gpt_mode):
        user = await self.db.get_user(message.from_user.id)
        if user is None:
            user = await self.db.create_user(message.from_user.id)
        user.gpt_mode = gpt_mode
        await self.db.update_user(user)
        await message.answer('👌')

    async def handle_message(self, message: types.Message):
        if message.text is None:
            return

        if message.text[0] == '/':
            command, *params = [m.strip() for m in message.text[1:].split(' ')]
            if command == 'reset':
                await self.reset_dialog(message)
                return
            if command == 'gpt3':
                await self.set_current_model(message, GptModel.GPT_35_TURBO)
                return
            if command == 'gpt4':
                await self.set_current_model(message, GptModel.GPT_4)
                return
            if command == 'setmode':
                mode = params[0] if len(params) == 1 else None
                if mode is None or mode not in settings.gpt_mode.keys():
                    available_modes = ', '.join(list(settings.gpt_mode.keys()))
                    await message.answer(f'Define mode: {available_modes}')
                    return
                await self.set_current_mode(message, mode)
                return

        await self.simple_answer(message)
