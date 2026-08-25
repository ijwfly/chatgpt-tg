import json
import os
import time
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

# --- Patch settings BEFORE any app code imports them ---
import settings
from app.storage.user_role import UserRole

# Test overrides
settings.OPENAI_TOKEN = 'test-openai-key'
settings.TELEGRAM_BOT_TOKEN = '123456:TEST-TOKEN'
settings.ANTHROPIC_TOKEN = ''
settings.OPENROUTER_TOKEN = ''
settings.POSTGRES_HOST = os.environ.get('POSTGRES_HOST', 'localhost')
settings.POSTGRES_PORT = int(os.environ.get('POSTGRES_PORT', '15432'))
settings.POSTGRES_USER = os.environ.get('POSTGRES_USER', 'postgres')
settings.POSTGRES_PASSWORD = os.environ.get('POSTGRES_PASSWORD', 'password')
settings.POSTGRES_DATABASE = os.environ.get('POSTGRES_DATABASE', 'chatgpttg')
settings.USER_ROLE_DEFAULT = UserRole.ADMIN
settings.USER_ROLE_BOT_ACCESS = UserRole.STRANGER
settings.ENABLE_WOLFRAMALPHA = False
settings.ENABLE_USER_ROLE_MANAGER_CHAT = False
settings.MCP_SERVERS = []
settings.EXTRA_MODELS = []
settings.WEB_AGENT_MODEL = ''  # web sub-agents use the user's model, which tests mock
settings.IMAGE_PROXY_URL = 'http://localhost'
settings.IMAGE_PROXY_PORT = 18321

# Now clear model cache so it picks up test settings
from app.llm_models import get_models
get_models.cache_clear()

from aiogram import Bot, Dispatcher
from aiogram.client.default import Default
from aiogram.client.session.base import BaseSession
from app.bot.telegram_bot import TelegramBot
from app.storage.db import DBFactory, DB
from app.openai_helpers.llm_client_factory import LLMClientFactory
from tests.helpers.bot_spy import BotSpy

import asyncpg

# ---- Message ID counter for bot request mock ----
_bot_message_id = 5000


def _fake_telegram_result(method: str, data: dict):
    """Builds a valid Telegram API result dict for an outgoing request (no network)."""
    global _bot_message_id
    _bot_message_id += 1

    if method in ('sendMessage', 'editMessageText', 'sendPhoto', 'sendDocument', 'sendVoice', 'editMessageReplyMarkup'):
        return {
            'message_id': _bot_message_id,
            'from': {'id': 0, 'is_bot': True, 'first_name': 'Bot'},
            'chat': {'id': data.get('chat_id', 12345), 'type': 'private'},
            'date': int(time.time()),
            'text': data.get('text', '') or data.get('caption', '') or '',
        }
    elif method == 'getMe':
        return {
            'id': 0,
            'is_bot': True,
            'first_name': 'TestBot',
            'username': 'test_bot',
        }
    else:
        # sendChatAction, deleteMessage, answerCallbackQuery, setMyCommands, ...
        return True


def _method_data(method) -> dict:
    """Request payload of an aiogram TelegramMethod as a plain dict (camelCase-free, no Default sentinels)."""
    data = {}
    for key, value in method.model_dump(exclude_none=True).items():
        if isinstance(value, Default):
            continue
        data[key] = value
    return data


class MockedSession(BaseSession):
    """aiogram session that never talks to Telegram.

    Every outgoing request is recorded as (api_method, data) in `requests`, and the
    (api_method, data, result) triple in `responses`, so tests can find out which telegram
    message id the bot got back for a sent message.
    """

    def __init__(self):
        super().__init__()
        self.requests = []
        self.responses = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        api_method = method.__api_method__
        data = _method_data(method)
        self.requests.append((api_method, data))
        result = _fake_telegram_result(api_method, data)
        self.responses.append((api_method, data, result))
        response = self.check_response(
            bot=bot, method=method, status_code=200, content=json.dumps({'ok': True, 'result': result}),
        )
        return response.result

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        yield b''


# ---- Fixtures ----

@pytest.fixture(scope='session')
def event_loop():
    """Single event loop for the entire test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope='session')
async def db_pool(event_loop):
    """Session-scoped connection pool."""
    dsn = f'postgres://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DATABASE}'
    pool = await asyncpg.create_pool(dsn)
    # Override DB default so test users get gpt-3.5-turbo (matches test mock setup)
    await pool.execute("ALTER TABLE chatgpttg.user ALTER COLUMN current_model SET DEFAULT 'gpt-3.5-turbo'")
    yield pool
    await pool.close()


@pytest_asyncio.fixture(scope='session')
async def db(db_pool):
    """Session-scoped DB instance."""
    return DB(db_pool)


@pytest_asyncio.fixture(autouse=True)
async def clean_db(db_pool):
    """Truncate all tables after each test."""
    yield
    tables = [
        'chatgpttg.tts_usage',
        'chatgpttg.image_generation_usage',
        'chatgpttg.whisper_usage',
        'chatgpttg.completion_usage',
        'chatgpttg.message',
        'chatgpttg.scheduled_task',
        'chatgpttg.plan',
        'chatgpttg.user',
    ]
    for table in tables:
        await db_pool.execute(f'DELETE FROM {table}')


@pytest.fixture
def mock_bot():
    """Bot whose session records requests instead of calling Telegram."""
    session = MockedSession()
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN, session=session)
    bot.sent_responses = session.responses
    return bot


@pytest.fixture
def spy(mock_bot):
    return BotSpy(mock_bot)


@pytest_asyncio.fixture
async def bot_app(mock_bot, db, db_pool):
    """Full bot application: TelegramBot + Dispatcher, initialized."""
    dp = Dispatcher()
    telegram_bot = TelegramBot(mock_bot, dp)

    # Patch Timer to be near-instant
    with patch('app.bot.utils.Timer.__init__', lambda self, timeout=0.3: (
        setattr(self, 'timeout', 0.001) or
        setattr(self, '_current_timeout', 0.001) or
        setattr(self, 'step', 0.0001)
    )):
        # Clear LLM client cache
        old_clients = LLMClientFactory._model_clients.copy()
        LLMClientFactory._model_clients.clear()

        # Clear model cache
        get_models.cache_clear()

        # Inject our test pool into DBFactory so on_startup uses it
        DBFactory.connection_pool = db_pool

        # aiogram 3 binds the bot to every update passed through dp.feed_update(bot, update),
        # so no ContextVar tricks are needed for message.bot to work across tasks.
        await telegram_bot.on_startup()

        yield telegram_bot, dp, mock_bot

        # Stop scheduled tasks but DON'T close the DB pool
        if telegram_bot.scheduler_service:
            await telegram_bot.scheduler_service.stop()
        if telegram_bot.monthly_usage_task:
            await telegram_bot.monthly_usage_task.stop()

        LLMClientFactory._model_clients = old_clients
        get_models.cache_clear()
