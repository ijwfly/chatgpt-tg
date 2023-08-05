from aiogram import types, Bot, Dispatcher

import settings
from app.storage.db import User, DB
from app.storage.user_role import UserRole, check_role


SET_ROLE_COMMAND = 'setrole'
UPDATE_INFO_COMMAND = 'updinfo'


class UserRoleManager:
    def __init__(self, bot: Bot, dispatcher: Dispatcher, db: DB):
        self.bot = bot
        self.dispatcher = dispatcher
        self.db = db
        self.dispatcher.register_callback_query_handler(
            self.setrole_callback, lambda c: SET_ROLE_COMMAND in c.data,
        )
        self.dispatcher.register_callback_query_handler(
            self.updaterole_callback, lambda c: UPDATE_INFO_COMMAND in c.data,
        )

    @staticmethod
    def get_keyboard(user: User):
        keyboard = types.InlineKeyboardMarkup()

        for role in UserRole:
            callback_data = f'{SET_ROLE_COMMAND}.{user.telegram_id}.{role.value}'
            if role == user.role:
                keyboard.add(types.InlineKeyboardButton(text=f'<{role.value}>', callback_data=callback_data))
            else:
                keyboard.add(types.InlineKeyboardButton(text=role.value, callback_data=callback_data))
        keyboard.add(types.InlineKeyboardButton(text='🔄', callback_data=f'{UPDATE_INFO_COMMAND}.{user.telegram_id}'))
        return keyboard

    @staticmethod
    def user_to_string(user):
        result = [f'*User Id*: {user.id}', f'*Telegram Id*: {user.telegram_id}']
        if user.full_name:
            result.append(f'*Full name*: {user.full_name}')
        if user.username:
            result.append(f'*Username*: @{user.username}')
        result.append(f'*Role*: {user.role.value}')
        return '\n'.join(result)

    @classmethod
    async def send_new_user_to_admin(cls, message: types.Message, user: User):
        bot = message.bot
        text = cls.user_to_string(user)
        await bot.send_message(
            settings.USER_ROLE_MANAGER_CHAT_ID, text, reply_markup=cls.get_keyboard(user), parse_mode=types.ParseMode.MARKDOWN
        )

    async def update_message(self, message: types.Message, user: User):
        text = self.user_to_string(user)
        await message.edit_text(text, reply_markup=self.get_keyboard(user), parse_mode=types.ParseMode.MARKDOWN)

    async def setrole_callback(self, callback_query: types.CallbackQuery):
        command, tg_user_id, role_value = callback_query.data.split('.')
        tg_user_id = int(tg_user_id)
        user = await self.db.get_user(tg_user_id)
        user_had_access = check_role(settings.BOT_ACCESS_ROLE_LEVEL, user.role)
        user.role = UserRole(role_value)
        await self.db.update_user(user)
        await self.bot.answer_callback_query(callback_query.id)
        await self.update_message(callback_query.message, user)
        if check_role(settings.BOT_ACCESS_ROLE_LEVEL, user.role) and not user_had_access:
            await self.bot.send_message(tg_user_id, f'You have been granted access to the bot.')

    async def updaterole_callback(self, callback_query: types.CallbackQuery):
        command, tg_user_id = callback_query.data.split('.')
        tg_user_id = int(tg_user_id)
        user = await self.db.get_user(tg_user_id)
        await self.bot.answer_callback_query(callback_query.id)
        await self.update_message(callback_query.message, user)