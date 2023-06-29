import json
from datetime import datetime

import asyncpg
import pydantic

from app.openai_helpers.chatgpt import DialogueMessage, GptModel


class User(pydantic.BaseModel):
    id: int
    telegram_id: int


class Dialog(pydantic.BaseModel):
    id: int
    user_id: int
    chat_id: int
    cdate: datetime
    is_active: bool
    model: str


class Message(pydantic.BaseModel):
    id: int
    dialog_id: int
    user_id: int
    message: DialogueMessage
    cdate: datetime


class DB:
    def __init__(self, connection_pool: asyncpg.Pool):
        self.connection_pool = connection_pool

    async def get_user(self, telegram_user_id):
        sql = '''SELECT * FROM chatgpttg.user WHERE telegram_id = $1'''
        record = await self.connection_pool.fetchrow(sql, telegram_user_id)
        if record is None:
            return None
        return User(**record)

    async def create_user(self, telegram_user_id):
        sql = '''INSERT INTO chatgpttg.user (telegram_id) VALUES ($1) RETURNING *'''
        return User(**await self.connection_pool.fetchrow(sql, telegram_user_id))

    async def get_active_dialog(self, user_id):
        sql = '''SELECT * FROM chatgpttg.dialog WHERE user_id = $1 AND is_active = TRUE'''
        record = await self.connection_pool.fetchrow(sql, user_id)
        if record is None:
            return None
        return Dialog(**record)

    async def create_active_dialog(self, user_id, chat_id, model=GptModel.GPT_35_TURBO):
        sql = '''INSERT INTO chatgpttg.dialog (user_id, chat_id, model) VALUES ($1, $2, $3) RETURNING *'''
        return Dialog(**await self.connection_pool.fetchrow(sql, user_id, chat_id, model))

    async def get_dialog_messages(self, dialog_id):
        sql = '''SELECT * FROM chatgpttg.message WHERE dialog_id = $1 ORDER BY cdate ASC'''
        records = await self.connection_pool.fetch(sql, dialog_id)
        if records is None:
            return []
        result = []
        for record in records:
            record = dict(record)
            record['message'] = json.loads(record['message'])
            result.append(record)
        return [Message(**record) for record in result]

    async def create_dialog_message(self, dialog_id, user_id, message: DialogueMessage):
        sql = '''INSERT INTO chatgpttg.message (dialog_id, user_id, message) VALUES ($1, $2, $3) RETURNING *'''
        openai_message = json.dumps(message.openai_message())
        record = await self.connection_pool.fetchrow(sql, dialog_id, user_id, openai_message)
        record = dict(record)
        record['message'] = json.loads(record['message'])
        return Message(**record)

    async def deactivate_active_dialog(self, user_id):
        sql = '''UPDATE chatgpttg.dialog SET is_active = FALSE WHERE user_id = $1 AND is_active = TRUE RETURNING *'''
        return Dialog(**await self.connection_pool.fetchrow(sql, user_id))


class DBFactory:
    connection_pool = None

    @classmethod
    async def create_database(cls, user, password, host, port, database) -> DB:
        if cls.connection_pool is None:
            dsn = f'postgres://{user}:{password}@{host}:{port}/{database}'
            cls.connection_pool = await asyncpg.create_pool(dsn=dsn)
        return DB(cls.connection_pool)

    @classmethod
    async def close_database(cls):
        if cls.connection_pool is not None:
            await cls.connection_pool.close()
