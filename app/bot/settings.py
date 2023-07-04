from app import settings

from aiogram import Bot, types, Dispatcher

from app.storage.db import User, DB


GPT_MODELS_OPTIONS = ['gpt-3.5-turbo', 'gpt-4']


class ChoiceSetting:
    def __init__(self, name: str, model_field: str, options: list):
        self.name = name
        self.model_field = model_field
        self.options = options

    def get_button_string(self, user: User):
        current_value = getattr(user, self.model_field)
        return f"{self.name}: {current_value}"

    def toggle(self, user: User):
        current_value = getattr(user, self.model_field)
        current_index = self.options.index(current_value)
        new_index = (current_index + 1) % len(self.options)
        new_value = self.options[new_index]
        setattr(user, self.model_field, new_value)
        return user


class Settings:
    def __init__(self, bot: Bot, dispatcher: Dispatcher, db: DB):
        self.bot = bot
        self.dispatcher = dispatcher
        self.db = db
        self.settings = {
            'current_model': ChoiceSetting('Current model', 'current_model', GPT_MODELS_OPTIONS),
            'gpt_mode': ChoiceSetting('GPT mode', 'gpt_mode', list(settings.gpt_mode.keys()))
        }
        self.dispatcher.register_callback_query_handler(self.process_callback, lambda c: c.data in self.settings or c.data == 'hide')

    async def send_settings(self, message: types.Message):
        user = await self.db.get_or_create_user(message.from_user.id)
        await message.answer("*Settings*", reply_markup=self.get_keyboard(user), parse_mode=types.ParseMode.MARKDOWN)

    def get_keyboard(self, user: User):
        keyboard = types.InlineKeyboardMarkup()
        for setting_name, setting_obj in self.settings.items():
            text = setting_obj.get_button_string(user)
            keyboard.add(types.InlineKeyboardButton(text=text, callback_data=setting_name))
        keyboard.add(types.InlineKeyboardButton(text='Hide settings', callback_data='hide'))
        return keyboard

    def toggle_setting(self, user: User, setting: str):
        setting = self.settings[setting]
        user = setting.toggle(user)
        return user

    async def process_callback(self, callback_query: types.CallbackQuery):
        if callback_query.data == 'hide':
            await self.bot.delete_message(
                chat_id=callback_query.from_user.id,
                message_id=callback_query.message.message_id
            )
        else:
            user = await self.db.get_or_create_user(callback_query.from_user.id)
            user = self.toggle_setting(user, callback_query.data)
            await self.db.update_user(user)

            await self.bot.answer_callback_query(callback_query.id, text=f"Settings saved")
            await self.bot.edit_message_reply_markup(
                chat_id=callback_query.from_user.id,
                message_id=callback_query.message.message_id,
                reply_markup=self.get_keyboard(user)
            )
