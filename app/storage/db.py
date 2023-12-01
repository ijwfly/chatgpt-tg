import json
from collections import defaultdict
from datetime import datetime, date
from enum import Enum
from typing import List, Optional

import settings
from app.openai_helpers.chatgpt import DialogMessage, CompletionUsage
from app.storage.user_role import UserRole

import asyncpg
import pydantic


class User(pydantic.BaseModel):
    id: int
    telegram_id: int
    current_model: str
    gpt_mode: str
    forward_as_prompt: bool
    voice_as_prompt: bool
    use_functions: bool
    auto_summarize: bool
    full_name: Optional[str]
    username: Optional[str]
    role: Optional[UserRole]
    streaming_answers: bool
    function_call_verbose: bool


class MessageType(Enum):
    MESSAGE = 'message'
    SUMMARY = 'summary'
    RESET = 'reset'


class Message(pydantic.BaseModel):
    id: int
    user_id: int
    message: DialogMessage
    cdate: datetime  # message creation date
    activation_dtime: datetime  # last interaction with message
    previous_message_ids: List[int]  # ids of previous messages in the branch of subdialog
    tg_chat_id: int
    tg_message_id: int
    message_type: MessageType


class DB:
    def __init__(self, connection_pool: asyncpg.Pool):
        self.connection_pool = connection_pool

    async def iterate_users(self):
        sql = 'SELECT * FROM chatgpttg.user'
        records = await self.connection_pool.fetch(sql)
        for record in records:
            yield User(**record)

    async def get_or_create_user(self, telegram_user_id):
        user = await self.get_user(telegram_user_id)
        if user is None:
            user = await self.create_user(telegram_user_id, settings.USER_ROLE_DEFAULT)
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
        voice_as_prompt = $4, use_functions = $5, auto_summarize = $6,
        full_name = $7, username = $8, role = $9, streaming_answers = $10,
        function_call_verbose = $11 WHERE id = $12 RETURNING *'''
        return User(**await self.connection_pool.fetchrow(
            sql, user.current_model, user.gpt_mode, user.forward_as_prompt,
            user.voice_as_prompt, user.use_functions, user.auto_summarize,
            user.full_name, user.username, user.role.value, user.streaming_answers,
            user.function_call_verbose, user.id,
        ))

    async def create_user(self, telegram_user_id: int, role: UserRole):
        sql = 'INSERT INTO chatgpttg.user (telegram_id, role) VALUES ($1, $2) RETURNING *'
        return User(**await self.connection_pool.fetchrow(sql, telegram_user_id, role.value))

    async def get_telegram_message(self, tg_chat_id: int, tg_message_id: int):
        sql = 'SELECT * FROM chatgpttg.message WHERE tg_chat_id = $1 AND tg_message_id = $2'
        tg_message_record = await self.connection_pool.fetchrow(sql, tg_chat_id, tg_message_id)
        if tg_message_record is None:
            return None
        tg_message_record = dict(tg_message_record)
        tg_message_record['message'] = json.loads(tg_message_record['message'])
        return Message(**tg_message_record)

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

    async def get_last_message(self, user_id, tg_chat_id) -> Message:
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

    async def create_message(self, user_id, tg_chat_id, tg_message_id, message: DialogMessage,
                             previous_messages: List[Message] = None, message_type: MessageType = MessageType.MESSAGE):
        if previous_messages is None:
            previous_messages = []

        sql = 'INSERT INTO chatgpttg.message (user_id, message, previous_message_ids, tg_chat_id, tg_message_id, message_type) VALUES ($1, $2, $3, $4, $5, $6) RETURNING *'
        openai_message = json.dumps(message.openai_message())
        previous_message_ids = [m.id for m in previous_messages]

        record = await self.connection_pool.fetchrow(sql, user_id, openai_message, previous_message_ids,
                                                     tg_chat_id, tg_message_id, message_type.value)
        record = dict(record)
        record['message'] = json.loads(record['message'])
        return Message(**record)

    async def create_reset_message(self, user_id, tg_chat_id):
        tg_message_id = -1
        message = '{}'
        sql = 'INSERT INTO chatgpttg.message (user_id, tg_chat_id, tg_message_id, message, message_type) VALUES ($1, $2, $3, $4, $5) RETURNING *'
        await self.connection_pool.fetchrow(sql, user_id, tg_chat_id, tg_message_id, message, 'reset')
        return

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
        audio_seconds = record['audio_seconds']
        if audio_seconds is None:
            return 0
        return audio_seconds

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

    async def get_all_users_completion_usage(self, month_date: date = None):
        if not month_date:
            month_date = datetime.now(settings.POSTGRES_TIMEZONE).date()

        year, month = month_date.year, month_date.month

        sql = f'''
        SELECT u.telegram_id, u.username, u.full_name, cu.model, 
           SUM(cu.prompt_tokens) AS prompt_tokens, 
           SUM(cu.completion_tokens) AS completion_tokens,
           SUM(cu.total_tokens) AS total_tokens
        FROM chatgpttg.completion_usage cu
        JOIN chatgpttg.user u ON cu.user_id = u.id
        WHERE EXTRACT(YEAR FROM cu.cdate) = {year} AND EXTRACT(MONTH FROM cu.cdate) = {month}
        GROUP BY u.id, cu.model;
        '''
        records = await self.connection_pool.fetch(sql)
        result = defaultdict(list)
        for record in records:
            telegram_id = record['telegram_id']
            full_name = record['full_name'] if record['full_name'] else None
            username = f"@{record['username']}" if record['username'] else None
            name = ' - '.join([n for n in [full_name, username] if n is not None])
            name = f'[{telegram_id}] {name}' if name else f'[{telegram_id}]'
            result[name].append(CompletionUsage(**dict(record)))
        return result

    async def get_all_users_whisper_usage(self, month_date: date = None):
        if not month_date:
            month_date = datetime.now(settings.POSTGRES_TIMEZONE).date()

        year, month = month_date.year, month_date.month

        sql = f'''
        SELECT u.telegram_id, u.username, u.full_name, 
           SUM(wu.audio_seconds) AS audio_seconds
        FROM chatgpttg.whisper_usage wu
        JOIN chatgpttg.user u ON wu.user_id = u.id
        WHERE EXTRACT(YEAR FROM wu.cdate) = {year} AND EXTRACT(MONTH FROM wu.cdate) = {month}
        GROUP BY u.id;
        '''
        records = await self.connection_pool.fetch(sql)
        result = {}
        for record in records:
            telegram_id = record['telegram_id']
            full_name = record['full_name'] if record['full_name'] else ''
            username = f"@{record['username']}" if record['username'] else ''
            name = ' - '.join([full_name, username])
            name = f'[{telegram_id}] {name}' if name else f'[{telegram_id}]'
            result[name] = record['audio_seconds']
        return result


class DBFactory:
    connection_pool = None

    @classmethod
    async def create_database(cls, user, password, host, port, database) -> DB:
        if cls.connection_pool is None:
            dsn = f'postgres://{user}:{password}@{host}:{port}/{database}'
            cls.connection_pool = await asyncpg.create_pool(dsn)

        return DB(cls.connection_pool)

    @classmethod
    async def close_database(cls):
        if cls.connection_pool is not None:
            await cls.connection_pool.close()
