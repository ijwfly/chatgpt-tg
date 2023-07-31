import json
from datetime import datetime
from typing import List

import asyncpg
import pydantic

from app.openai_helpers.chatgpt import DialogMessage, GptModel, CompletionUsage


class User(pydantic.BaseModel):
    id: int
    telegram_id: int
    current_model: str
    gpt_mode: str
    forward_as_prompt: bool
    voice_as_prompt: bool
    use_functions: bool
    dynamic_dialog: bool


class Dialog(pydantic.BaseModel):
    id: int
    user_id: int
    chat_id: int
    cdate: datetime
    is_active: bool


class Message(pydantic.BaseModel):
    id: int
    dialog_id: int
    user_id: int
    message: DialogMessage
    cdate: datetime  # message creation date
    activation_dtime: datetime  # last interaction with message
    previous_message_ids: List[int]  # ids of previous messages in the branch of subdialog
    is_subdialog: bool  # is this message a part of subdialog (applicable only for default dialog manager)
    tg_chat_id: int
    tg_message_id: int


class DB:
    def __init__(self, connection_pool: asyncpg.Pool):
        self.connection_pool = connection_pool

    async def get_or_create_user(self, telegram_user_id):
        user = await self.get_user(telegram_user_id)
        if user is None:
            user = await self.create_user(telegram_user_id)
        return user

    async def get_user(self, telegram_user_id):
        sql = 'SELECT * FROM chatgpttg.user WHERE telegram_id = $1'
        record = await self.connection_pool.fetchrow(sql, telegram_user_id)
        if record is None:
            return None
        return User(**record)

    async def update_user(self, user: User):
        sql = '''UPDATE chatgpttg.user 
        SET current_model = $1, gpt_mode = $2, forward_as_prompt = $3,
        voice_as_prompt = $4, use_functions = $5, dynamic_dialog = $6 WHERE id = $7 RETURNING *'''
        return User(**await self.connection_pool.fetchrow(
            sql, user.current_model, user.gpt_mode, user.forward_as_prompt,
            user.voice_as_prompt, user.use_functions, user.dynamic_dialog, user.id,
        ))

    async def create_user(self, telegram_user_id):
        sql = 'INSERT INTO chatgpttg.user (telegram_id) VALUES ($1) RETURNING *'
        return User(**await self.connection_pool.fetchrow(sql, telegram_user_id))

    async def get_active_dialog(self, user_id):
        sql = 'SELECT * FROM chatgpttg.dialog WHERE user_id = $1 AND is_active = TRUE'
        record = await self.connection_pool.fetchrow(sql, user_id)
        if record is None:
            return None
        return Dialog(**record)

    async def create_active_dialog(self, user_id, chat_id):
        sql = 'INSERT INTO chatgpttg.dialog (user_id, chat_id) VALUES ($1, $2) RETURNING *'
        return Dialog(**await self.connection_pool.fetchrow(sql, user_id, chat_id, model))

    async def get_dialog_messages(self, dialog_id):
        sql = 'SELECT * FROM chatgpttg.message WHERE dialog_id = $1 AND is_subdialog = FALSE ORDER BY cdate ASC'
        records = await self.connection_pool.fetch(sql, dialog_id)
        if records is None:
            return []
        result = []
        for record in records:
            record = dict(record)
            record['message'] = json.loads(record['message'])
            result.append(record)
        return [Message(**record) for record in result]

    async def get_telegram_message(self, tg_chat_id, tg_message_id):
        sql = 'SELECT * FROM chatgpttg.message WHERE tg_chat_id = $1 AND tg_message_id = $2'
        tg_message_record = await self.connection_pool.fetchrow(sql, tg_chat_id, tg_message_id)
        if tg_message_record is None:
            return None
        tg_message_record = dict(tg_message_record)
        tg_message_record['message'] = json.loads(tg_message_record['message'])
        return Message(**tg_message_record)

    async def get_subdialog_messages(self, tg_chat_id, tg_message_id):
        tg_root_message = await self.get_telegram_message(tg_chat_id, tg_message_id)
        if tg_root_message is None:
            raise ValueError("subdialog root message not found")
        sql = 'SELECT * FROM chatgpttg.message WHERE id = ANY($1::bigint[]) ORDER BY cdate ASC'
        records = await self.connection_pool.fetch(sql, tg_root_message.previous_message_ids)
        if records is None:
            return [tg_root_message]

        result = []
        for record in records:
            record = dict(record)
            record['message'] = json.loads(record['message'])
            result.append(record)

        result = [Message(**record) for record in result]
        result.append(tg_root_message)
        return result

    async def get_messages_by_ids(self, message_ids: List[int]):
        sql = 'SELECT * FROM chatgpttg.message WHERE id = ANY($1::bigint[]) ORDER BY cdate ASC'
        records = await self.connection_pool.fetch(sql, message_ids)
        if records is None:
            return []

        result = []
        for record in records:
            record = dict(record)
            record['message'] = json.loads(record['message'])
            result.append(record)

        result = [Message(**record) for record in result]
        return result

    async def get_last_message(self, user_id, tg_chat_id):
        sql = 'SELECT * FROM chatgpttg.message WHERE user_id = $1 AND tg_chat_id = $2 ORDER BY cdate DESC LIMIT 1'
        record = await self.connection_pool.fetchrow(sql, user_id, tg_chat_id)
        if record is None:
            return None
        record = dict(record)
        record['message'] = json.loads(record['message'])
        return Message(**record)

    async def update_activation_dtime(self, message_ids: List[int]):
        sql = 'UPDATE chatgpttg.message SET activation_dtime = NOW() WHERE id = ANY($1::bigint[])'
        await self.connection_pool.execute(sql, message_ids)

    async def create_dialog_message(self, dialog_id, user_id, tg_chat_id, tg_message_id, message: DialogMessage,
                                    previous_messages: List[DialogMessage] = None, is_subdialog=False):
        if previous_messages is None:
            previous_messages = []

        sql = 'INSERT INTO chatgpttg.message (dialog_id, user_id, message, is_subdialog, previous_message_ids, tg_chat_id, tg_message_id) VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *'
        openai_message = json.dumps(message.openai_message())
        previous_message_ids = [m.id for m in previous_messages]

        record = await self.connection_pool.fetchrow(sql, dialog_id, user_id, openai_message, is_subdialog,
                                                     previous_message_ids, tg_chat_id, tg_message_id)
        record = dict(record)
        record['message'] = json.loads(record['message'])
        return Message(**record)

    async def deactivate_active_dialog(self, user_id):
        sql = 'UPDATE chatgpttg.dialog SET is_active = FALSE WHERE user_id = $1 AND is_active = TRUE RETURNING *'
        record = await self.connection_pool.fetchrow(sql, user_id)
        if record is None:
            return None
        return Dialog(**record)

    async def create_completion_usage(self, user_id, prompt_tokens, completion_tokens, total_tokens, model) -> None:
        sql = 'INSERT INTO chatgpttg.completion_usage (user_id, prompt_tokens, completion_tokens, total_tokens, model) VALUES ($1, $2, $3, $4, $5)'
        await self.connection_pool.fetchrow(sql, user_id, prompt_tokens, completion_tokens, total_tokens, model)

    async def create_whisper_usage(self, user_id, audio_seconds) -> None:
        sql = 'INSERT INTO chatgpttg.whisper_usage (user_id, audio_seconds) VALUES ($1, $2)'
        await self.connection_pool.fetchrow(sql, user_id, audio_seconds)

    async def get_user_current_month_whisper_usage(self, user_id):
        sql = '''SELECT SUM(audio_seconds) AS audio_seconds
            FROM chatgpttg.whisper_usage
            WHERE user_id = $1 AND date_trunc('month', cdate) = date_trunc('month', current_date)
        '''
        record = await self.connection_pool.fetchrow(sql, user_id)
        if record is None:
            return 0
        return record['audio_seconds']

    async def get_user_current_month_completion_usage(self, user_id):
        sql = '''
        SELECT model, 
           SUM(prompt_tokens) AS prompt_tokens, 
           SUM(completion_tokens) AS completion_tokens,
           SUM(total_tokens) AS total_tokens
        FROM chatgpttg.completion_usage
        WHERE user_id = $1 AND
          date_trunc('month', cdate) = date_trunc('month', current_date)
        GROUP BY model;
        '''
        records = await self.connection_pool.fetch(sql, user_id)
        if not records:
            return []
        return [CompletionUsage(**dict(record)) for record in records]


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
