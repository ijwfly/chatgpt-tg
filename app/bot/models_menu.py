from aiogram import Bot, types, Dispatcher

from app.llm_models import get_models, get_model_by_name
from app.storage.db import User, DB
from app.storage.user_role import check_access_conditions, UserRole

MODELS_PREFIX = 'models'
HIDE_COMMAND = 'hide'


class ModelsMenu:
    def __init__(self, bot: Bot, dispatcher: Dispatcher, db: DB):
        self.bot = bot
        self.dispatcher = dispatcher
        self.db = db
        self.models = get_models()
        self.dispatcher.register_callback_query_handler(self.process_callback, lambda c: MODELS_PREFIX in c.data)

    async def send_menu(self, message: types.Message, user: User):
        await message.answer(self.get_model_info(user), reply_markup=self.get_keyboard(user), parse_mode=types.ParseMode.MARKDOWN)

    @staticmethod
    def is_model_available_for_user(llm_model, user: User):
        if not check_access_conditions(llm_model.minimum_user_role, user.role):
            return False
        return True

    def get_model_info(self, user: User) -> str:
        llm_model = get_model_by_name(user.current_model)
        info = f"*Current model*: {llm_model.model_readable_name}\n"
        info += f"*Model name*: {llm_model.model_name}\n"

        input_price = float(llm_model.model_price.input_tokens_price * 1000)
        output_price = float(llm_model.model_price.output_tokens_price * 1000)
        info += f"*Pricing*:\n"
        info += f"\t-\t${input_price:.2f} per 1M input tokens\n"
        info += f"\t-\t${output_price:.2f} per 1M output tokens\n"
        info += f"*Capabilities*:\n"
        info += f'\t-\t*Function calling*: {llm_model.capabilities.function_calling}\n'
        info += f'\t-\t*Tool calling*: {llm_model.capabilities.tool_calling}\n'
        info += f'\t-\t*Image processing*: {llm_model.capabilities.image_processing}\n'
        return info

    def get_keyboard(self, user: User):
        keyboard = types.InlineKeyboardMarkup()

        for model_name, llm_model in self.models.items():
            if not self.is_model_available_for_user(llm_model, user):
                continue
            model_readable_name = llm_model.model_readable_name
            if model_name == user.current_model:
                model_readable_name = f'< {model_readable_name} >'
            keyboard.add(types.InlineKeyboardButton(text=model_readable_name, callback_data=f'{MODELS_PREFIX}.{model_name}'))
        keyboard.add(types.InlineKeyboardButton(text='Hide menu', callback_data=f'{MODELS_PREFIX}.{HIDE_COMMAND}'))

        return keyboard

    def set_model(self, user: User, llm_model):
        if self.is_model_available_for_user(llm_model, user):
            user.current_model = llm_model.model_name
        return user

    async def process_callback(self, callback_query: types.CallbackQuery):
        _, *command = callback_query.data.split('.')
        command = '.'.join(command)
        if command == HIDE_COMMAND:
            await self.bot.delete_message(
                chat_id=callback_query.from_user.id,
                message_id=callback_query.message.message_id
            )
            await self.bot.answer_callback_query(callback_query.id)
        else:
            model_name = command
            user = await self.db.get_or_create_user(callback_query.from_user.id)
            llm_model = get_model_by_name(model_name)
            user = self.set_model(user, llm_model)
            await self.db.update_user(user)

            await self.bot.answer_callback_query(callback_query.id)
            await self.bot.edit_message_text(
                text=self.get_model_info(user),
                chat_id=callback_query.from_user.id,
                message_id=callback_query.message.message_id,
                reply_markup=self.get_keyboard(user),
                parse_mode=types.ParseMode.MARKDOWN
            )
