"""Unit tests for TypingWorker: the chat action indicator must never break the operation it decorates."""
import asyncio

import pytest
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendChatAction

from app.bot import utils
from app.bot.utils import TypingWorker


def _retry_after(seconds):
    return TelegramRetryAfter(
        method=SendChatAction(chat_id=1, action='typing'),
        message=f'Too Many Requests: retry after {seconds}', retry_after=seconds,
    )


class _BotStub:
    """Records chat actions; raises queued exceptions first."""

    def __init__(self, failures=()):
        self.calls = 0
        self.failures = list(failures)

    async def send_chat_action(self, chat_id, action):
        if self.failures:
            raise self.failures.pop(0)
        self.calls += 1
        return True


@pytest.fixture(autouse=True)
def fast_typing(monkeypatch):
    """Keep the worker loop from actually sleeping seconds between refreshes."""
    monkeypatch.setattr(utils, 'TYPING_DELAY', 0.01)
    monkeypatch.setattr(utils, 'TYPING_QUERIES_LIMIT', 100)


@pytest.mark.asyncio
async def test_flood_control_does_not_escape_the_context():
    bot = _BotStub(failures=[_retry_after(0.01)])

    async with TypingWorker(bot, 1).typing_context():
        await asyncio.sleep(0.05)

    # the flood error is swallowed and the worker keeps refreshing afterwards
    assert bot.calls >= 1


@pytest.mark.asyncio
async def test_api_error_does_not_escape_the_context():
    bot = _BotStub(failures=[TelegramNetworkError(method=SendChatAction(chat_id=1, action='typing'),
                                                 message='boom')])

    async with TypingWorker(bot, 1).typing_context():
        await asyncio.sleep(0.05)

    assert bot.calls >= 1


@pytest.mark.asyncio
async def test_flood_control_backs_off_for_retry_after():
    """A retry_after longer than the refresh delay must pause the loop, not spin on it."""
    bot = _BotStub(failures=[_retry_after(0.2)])

    worker = TypingWorker(bot, 1)
    await worker.start_typing()
    await asyncio.sleep(0.05)
    assert bot.calls == 0  # still holding off
    await asyncio.sleep(0.25)
    assert bot.calls >= 1
    await worker.stop_typing()


@pytest.mark.asyncio
async def test_body_exception_is_still_raised():
    bot = _BotStub()

    with pytest.raises(ValueError):
        async with TypingWorker(bot, 1).typing_context():
            raise ValueError('body failed')
