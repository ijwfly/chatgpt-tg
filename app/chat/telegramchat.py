from app.chat.dialog_manager import DialogManager

from aiogram import types


class TelegramChat:
    def __init__(self, db, chat_gpt):
        self.db = db
        self.chat_gpt = chat_gpt

    async def simple_answer(self, message: types.Message):
        dialog_manager = DialogManager(self.db)

        context_dialog_messages = await dialog_manager.process_dialog(message)
        input_dialog_message = await dialog_manager.prepare_input_message(message)

        response_dialog_message = await self.chat_gpt.send_user_message(input_dialog_message, context_dialog_messages)
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
        await message.answer('Dialog reseted')

    async def handle_message(self, message: types.Message):
        if message.text is None:
            return

        if message.text[0] == '/':
            command = message.text[1:]
            if command == 'reset':
                await self.reset_dialog(message)
                return

        await self.simple_answer(message)
