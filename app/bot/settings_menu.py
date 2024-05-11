import settings

from aiogram import Bot, types, Dispatcher

from app.llm_models import get_models
from app.storage.db import User, DB
from app.storage.user_role import check_access_conditions, UserRole

GPT_MODELS_OPTIONS = {
    'gpt-3.5-turbo': 'GPT-3.5',
    'gpt-4-turbo': 'GPT-4-Turbo',
}

ALL_MODELS_OPTIONS = list(get_models().keys())

TTS_VOICES = ['alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer']

SETTINGS_PREFIX = 'settings'
HIDE_COMMAND = 'hide'


class VisibleOptionsSetting:
    def __init__(self, model_field: str, options):
        self.model_field = model_field
        self.options = options

    def get_button_string(self, user: User):
        current_value = getattr(user, self.model_field)
        rendered_options = []
        for value, display_name in self.options.items():
            if current_value == value:
                rendered_options.append(f"<{display_name}>")
            else:
                rendered_options.append(display_name)
        return " | ".join(rendered_options)

    def toggle(self, user: User):
        current_value = getattr(user, self.model_field)
        options_values = list(self.options.keys())
        try:
            current_index = options_values.index(current_value)
            new_index = (current_index + 1) % len(options_values)
        except ValueError:
            # current value is not in options, set first option
            new_index = 0
        new_value = options_values[new_index]
        setattr(user, self.model_field, new_value)
        return user


class OnOffSetting:
    def __init__(self, name: str, model_field: str):
        self.model_field = model_field
        self.setting_name = name

    def get_button_string(self, user: User):
        current_value = getattr(user, self.model_field)
        if current_value:
            return f"{self.setting_name}: On"
        else:
            return f"{self.setting_name}: Off"

    def toggle(self, user: User):
        current_value = getattr(user, self.model_field)
        if current_value:
            setattr(user, self.model_field, False)
        else:
            setattr(user, self.model_field, True)
        return user


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
            'current_model': VisibleOptionsSetting('current_model', GPT_MODELS_OPTIONS),
            'all_models': ChoiceSetting('Model', 'current_model', ALL_MODELS_OPTIONS),
            'gpt_mode': ChoiceSetting('GPT mode', 'gpt_mode', list(settings.gpt_mode.keys())),
            'use_functions': OnOffSetting('Use functions', 'use_functions'),
            'image_generation': OnOffSetting('Image generation', 'image_generation'),
            'system_prompt_settings_enabled': OnOffSetting('User info saving', 'system_prompt_settings_enabled'),
            'tts-voice': ChoiceSetting('TTS voice', 'tts_voice', TTS_VOICES),
            'voice_as_prompt': OnOffSetting('Voice as prompt', 'voice_as_prompt'),
            'function_call_verbose': OnOffSetting('Verbose function calls', 'function_call_verbose'),
            'streaming_answers': OnOffSetting('Streaming answers', 'streaming_answers'),
            # 'auto_summarize': OnOffSetting('Auto summarize', 'auto_summarize'),
            # 'forward_as_prompt': OnOffSetting('Forward as prompt', 'forward_as_prompt'),
        }
        self.minimum_required_roles = {
            'current_model': settings.USER_ROLE_CHOOSE_MODEL,
            'all_models': UserRole.ADMIN,
            'image_generation': settings.USER_ROLE_IMAGE_GENERATION,
            'tts-voice': settings.USER_ROLE_TTS,
            'streaming_answers': settings.USER_ROLE_STREAMING_ANSWERS,
        }
        self.dispatcher.register_callback_query_handler(self.process_callback, lambda c: SETTINGS_PREFIX in c.data)

    async def send_settings(self, message: types.Message, user: User):
        await message.answer("Settings:", reply_markup=self.get_keyboard(user), parse_mode=types.ParseMode.MARKDOWN)

    def is_setting_available_for_user(self, setting_name: str, user: User):
        mininum_required_role = self.minimum_required_roles.get(setting_name)
        if mininum_required_role and not check_access_conditions(mininum_required_role, user.role):
            return False
        return True

    def get_keyboard(self, user: User):
        keyboard = types.InlineKeyboardMarkup()
        for setting_name, setting_obj in self.settings.items():
            if not self.is_setting_available_for_user(setting_name, user):
                continue

            text = setting_obj.get_button_string(user)
            keyboard.add(types.InlineKeyboardButton(text=text, callback_data=f'{SETTINGS_PREFIX}.{setting_name}'))
        keyboard.add(types.InlineKeyboardButton(text='Hide settings', callback_data=f'{SETTINGS_PREFIX}.{HIDE_COMMAND}'))
        return keyboard

    def toggle_setting(self, user: User, setting_name: str):
        if not self.is_setting_available_for_user(setting_name, user):
            return user
        setting_name = self.settings[setting_name]
        user = setting_name.toggle(user)
        return user

    async def process_callback(self, callback_query: types.CallbackQuery):
        _, command = callback_query.data.split('.')
        if command == HIDE_COMMAND:
            await self.bot.delete_message(
                chat_id=callback_query.from_user.id,
                message_id=callback_query.message.message_id
            )
            await self.bot.answer_callback_query(callback_query.id)
        else:
            setting = command
            user = await self.db.get_or_create_user(callback_query.from_user.id)
            user = self.toggle_setting(user, setting)
            await self.db.update_user(user)

            await self.bot.answer_callback_query(callback_query.id)
            await self.bot.edit_message_reply_markup(
                chat_id=callback_query.from_user.id,
                message_id=callback_query.message.message_id,
                reply_markup=self.get_keyboard(user)
            )
