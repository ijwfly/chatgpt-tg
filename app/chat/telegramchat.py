from app.openai_helpers.chatgpt import DialogueMessage

from aiogram import types


class TelegramChat:
    def __init__(self, db, chat_gpt):
        self.db = db
        self.chat_gpt = chat_gpt

    async def simple_answer(self, message: types.Message):
        user = await self.db.get_user(message.from_user.id)
        if user is None:
            user = await self.db.create_user(message.from_user.id)

        dialog = await self.db.get_active_dialog(user.id)
        if dialog is None:
            dialog = await self.db.create_active_dialog(user.id, message.chat.id)

        dialog_messages = await self.db.get_dialog_messages(dialog.id)
        dialog_messages = [d.message for d in dialog_messages]

        request_text = message.text
        dialogue_message = DialogueMessage(role="user", content=request_text)

        response = await self.chat_gpt.send_user_message(dialogue_message, dialog_messages)
        await message.answer(response.content)
        await self.db.create_dialog_message(dialog.id, user.id, dialogue_message)
        await self.db.create_dialog_message(dialog.id, user.id, response)

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
