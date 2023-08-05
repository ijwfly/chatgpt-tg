import settings
from app.bot.user_role_manager import UserRoleManager
from app.storage.db import DB
from app.storage.user_role import check_role

from aiogram import types
from aiogram.dispatcher.handler import CancelHandler
from aiogram.dispatcher.middlewares import BaseMiddleware


class UserMiddleware(BaseMiddleware):
    def __init__(self, db: DB):
        super().__init__()
        self.db = db

    async def on_pre_process_message(self, message: types.Message, data: dict):
        is_new_user = False

        user_id = message.from_user.id
        user = await self.db.get_user(user_id)
        if user is None:
            user = await self.db.create_user(user_id, settings.DEFAULT_USER_ROLE)
            is_new_user = True

        if user.role is None:
            user.role = settings.DEFAULT_USER_ROLE
            await self.db.update_user(user)

        full_name = message.from_user.full_name
        username = message.from_user.username
        if user.full_name != full_name or user.username != username:
            user.full_name = full_name
            user.username = username
            await self.db.update_user(user)

        if settings.ENABLE_USER_ROLE_MANAGER_CHAT and is_new_user:
            await UserRoleManager.send_new_user_to_admin(message, user)

        user_have_access = check_role(settings.BOT_ACCESS_ROLE_LEVEL, user.role)
        if not user_have_access:
            await message.answer(
                "You currently don't have access to this bot. You will be notified once the admin grants you access."
            )
            raise CancelHandler()

        data['user'] = user
