from aiogram import types
from aiogram.dispatcher.middlewares import BaseMiddleware

from app.storage.db import DB


class UserMiddleware(BaseMiddleware):
    def __init__(self, db: DB):
        super().__init__()
        self.db = db

    async def on_pre_process_message(self, message: types.Message, data: dict):
        user_id = message.from_user.id
        user = await self.db.get_or_create_user(user_id)

        full_name = message.from_user.full_name
        username = message.from_user.username
        if user.full_name != full_name or user.username != username:
            user.full_name = full_name
            user.username = username
            await self.db.update_user(user)

        data['user'] = user
