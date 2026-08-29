from typing import Any, Awaitable, Callable, Dict

import settings
from app.bot.user_role_manager import UserRoleManager
from app.storage.db import DB
from app.storage.user_role import check_access_conditions

from aiogram import BaseMiddleware
from aiogram.types import Message


class UserMiddleware(BaseMiddleware):
    """Loads (or creates) the DB user for every incoming message and injects it as the `user` handler argument.

    Messages from users without bot access are answered here and never reach the handlers.
    """

    def __init__(self, db: DB):
        super().__init__()
        self.db = db

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        message: Message,
        data: Dict[str, Any],
    ) -> Any:
        is_new_user = False

        user_id = message.from_user.id
        user = await self.db.get_user(user_id)
        if user is None:
            user = await self.db.create_user(user_id, settings.USER_ROLE_DEFAULT)
            is_new_user = True

        if user.role is None:
            user.role = settings.USER_ROLE_DEFAULT
            await self.db.update_user(user)

        full_name = message.from_user.full_name
        username = message.from_user.username
        if user.full_name != full_name or user.username != username:
            user.full_name = full_name
            user.username = username
            await self.db.update_user(user)

        if settings.ENABLE_USER_ROLE_MANAGER_CHAT and is_new_user:
            bot = data['bot']
            await UserRoleManager.send_new_user_to_admin(bot, user)

        user_have_access = check_access_conditions(settings.USER_ROLE_BOT_ACCESS, user.role)
        if not user_have_access:
            await message.answer(
                "You currently don't have access to this bot. You will be notified once the admin grants you access."
            )
            return None

        data['user'] = user
        return await handler(message, data)
