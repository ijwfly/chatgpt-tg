from aiogram import types
from aiogram.dispatcher.middlewares import BaseMiddleware

from app.storage.db import DB


class UserMiddleware(BaseMiddleware):
    def __init__(self, db: DB):
        super().__init__()
        self.db = db

    async def on_pre_process_message(self, message: types.Message, data: dict):
        user_id = message.from_user.id
        # Здесь вы можете получить пользователя из базы данных
        user = await self.db.get_or_create_user(user_id)
        data['user'] = user
