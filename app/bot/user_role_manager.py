from aiogram import types, Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder

import settings
from app.bot.utils import escape_tg_markdown
from app.storage.db import User, DB
from app.storage.user_role import UserRole, check_access_conditions


SET_ROLE_COMMAND = 'setrole'
UPDATE_INFO_COMMAND = 'updinfo'


class UserRoleManager:
    def __init__(self, bot: Bot, dispatcher: Dispatcher, db: DB):
        self.bot = bot
        self.dispatcher = dispatcher
        self.db = db
        self.dispatcher.callback_query.register(self.setrole_callback, F.data.contains(SET_ROLE_COMMAND))
        self.dispatcher.callback_query.register(self.updaterole_callback, F.data.contains(UPDATE_INFO_COMMAND))

    @staticmethod
    def get_keyboard(user: User):
        keyboard = InlineKeyboardBuilder()

        for role in UserRole:
            if role == UserRole.NOONE:
                # noone role is not assignable
                continue
            callback_data = f'{SET_ROLE_COMMAND}.{user.telegram_id}.{role.value}'
            if role == user.role:
                keyboard.add(types.InlineKeyboardButton(text=f'<{role.value}>', callback_data=callback_data))
            else:
                keyboard.add(types.InlineKeyboardButton(text=role.value, callback_data=callback_data))
        keyboard.add(types.InlineKeyboardButton(text='🔄', callback_data=f'{UPDATE_INFO_COMMAND}.{user.telegram_id}'))
        keyboard.adjust(1)
        return keyboard.as_markup()

    @staticmethod
    def user_to_string(user):
        result = [f'*User Id*: {user.id}', f'*Telegram Id*: {user.telegram_id}']
        if user.full_name:
            full_name = escape_tg_markdown(user.full_name)
            result.append(f'*Full name*: {full_name}')
        if user.username:
            username = escape_tg_markdown(user.username)
            result.append(f'*Username*: @{username}')
        result.append(f'*Role*: {user.role.value}')
        return '\n'.join(result)

    @classmethod
    async def send_new_user_to_admin(cls, bot: Bot, user: User):
        text = cls.user_to_string(user)
        text = '#admin\n' + text
        await bot.send_message(
            chat_id=settings.USER_ROLE_MANAGER_CHAT_ID, text=text,
            reply_markup=cls.get_keyboard(user), parse_mode=ParseMode.MARKDOWN,
        )

    async def update_message(self, message: types.Message, user: User):
        text = self.user_to_string(user)
        await message.edit_text(text, reply_markup=self.get_keyboard(user), parse_mode=ParseMode.MARKDOWN)

    @staticmethod
    def get_role_commands(user_role: UserRole):
        commands = []

        commands += [
            types.BotCommand(command="reset", description="reset current dialog"),
            types.BotCommand(command="settings", description="open settings menu"),
        ]

        if check_access_conditions(settings.USER_ROLE_CHOOSE_MODEL, user_role):
            commands += [
                types.BotCommand(command="models", description="open models menu"),
            ]

        commands.append(
            types.BotCommand(command="usage", description="show usage for current month"),
        )

        if check_access_conditions(settings.USER_ROLE_TTS, user_role):
            commands += [
                types.BotCommand(command="text2speech", description="generate voice from message"),
            ]

        if check_access_conditions(UserRole.ADMIN, user_role):
            commands += [
                types.BotCommand(command="usage_all", description="show usage for all users"),
            ]
        return commands

    async def set_user_commands(self, user: User, user_role=None):
        if user_role is None:
            user_role = user.role
        commands = self.get_role_commands(user_role)
        await self.bot.set_my_commands(commands, scope=types.BotCommandScopeChat(chat_id=user.telegram_id))

    async def setrole_callback(self, callback_query: types.CallbackQuery):
        command, tg_user_id, role_value = callback_query.data.split('.')
        tg_user_id = int(tg_user_id)
        user = await self.db.get_user(tg_user_id)
        user_had_access = check_access_conditions(settings.USER_ROLE_BOT_ACCESS, user.role)
        user.role = UserRole(role_value)
        await self.db.update_user(user)
        await callback_query.answer()
        await self.set_user_commands(user, user.role)
        await self.update_message(callback_query.message, user)
        if check_access_conditions(settings.USER_ROLE_BOT_ACCESS, user.role) and not user_had_access:
            await self.bot.send_message(chat_id=tg_user_id, text='You have been granted access to the bot.')

    async def updaterole_callback(self, callback_query: types.CallbackQuery):
        command, tg_user_id = callback_query.data.split('.')
        tg_user_id = int(tg_user_id)
        user = await self.db.get_user(tg_user_id)
        await callback_query.answer()
        await self.update_message(callback_query.message, user)
