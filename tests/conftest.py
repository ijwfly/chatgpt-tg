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
settings.VECTARA_RAG_ENABLED = False
settings.ENABLE_TODOIST_ADMIN_INTEGRATION = False
settings.ENABLE_OBSIDIAN_ECHO_ADMIN_INTEGRATION = False
settings.ENABLE_USER_ROLE_MANAGER_CHAT = False
settings.MCP_SERVERS = []
settings.EXTRA_MODELS = []
settings.HTTP_API_ENABLED = False
settings.IMAGE_PROXY_URL = 'http://localhost'
settings.IMAGE_PROXY_PORT = 18321

# Now clear model cache so it picks up test settings
from app.llm_models import get_models
get_models.cache_clear()

from aiogram import Bot, Dispatcher
from aiogram.types.base import TelegramObject
from app.bot.telegram_bot import TelegramBot
from app.storage.db import DBFactory, DB
from app.openai_helpers.llm_client_factory import LLMClientFactory
from tests.helpers.bot_spy import BotSpy

import asyncpg

# Store reference to the test bot for the property override
_test_bot_ref = None


# ---- Message ID counter for bot request mock ----
_bot_message_id = 5000


def _make_bot_request_handler():
    """Create an async handler for Bot.request that returns valid Telegram response dicts."""
    async def mock_request(method, data=None, **kwargs):
        global _bot_message_id
        _bot_message_id += 1

        if method in ('sendMessage', 'editMessageText', 'sendPhoto', 'editMessageReplyMarkup'):
            chat_id = 12345
            if data:
                chat_id = data.get('chat_id', 12345)
            return {
                'message_id': _bot_message_id,
                'from': {'id': 0, 'is_bot': True, 'first_name': 'Bot'},
                'chat': {'id': chat_id, 'type': 'private'},
                'date': int(time.time()),
                'text': data.get('text', '') if data else '',
            }
        elif method in ('sendChatAction', 'deleteMessage', 'answerCallbackQuery'):
            return True
        elif method == 'setMyCommands':
            return True
        elif method == 'getMe':
            return {
                'id': 0,
                'is_bot': True,
                'first_name': 'TestBot',
                'username': 'test_bot',
            }
        else:
            return True

    return mock_request


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
        'chatgpttg.user',
    ]
    for table in tables:
        await db_pool.execute(f'DELETE FROM {table}')


@pytest.fixture
def mock_bot():
    """Bot with mocked request method."""
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    bot.request = AsyncMock(side_effect=_make_bot_request_handler())
    return bot


@pytest.fixture
def spy(mock_bot):
    return BotSpy(mock_bot)


@pytest_asyncio.fixture
async def bot_app(mock_bot, db, db_pool):
    """Full bot application: TelegramBot + Dispatcher, initialized."""
    dp = Dispatcher(mock_bot)
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

        # Monkey-patch TelegramObject.bot to always return our mock bot
        # This avoids ContextVar issues across asyncio tasks
        global _test_bot_ref
        _test_bot_ref = mock_bot
        _original_bot_property = TelegramObject.bot.fget

        def _patched_bot(self):
            return _test_bot_ref

        TelegramObject.bot = property(_patched_bot)

        await telegram_bot.on_startup(None)

        yield telegram_bot, dp, mock_bot

        # Restore original property
        TelegramObject.bot = property(_original_bot_property)
        _test_bot_ref = None

        # Stop scheduled tasks but DON'T close the DB pool
        if telegram_bot.monthly_usage_task:
            await telegram_bot.monthly_usage_task.stop()

        LLMClientFactory._model_clients = old_clients
        get_models.cache_clear()
