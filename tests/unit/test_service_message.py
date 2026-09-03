"""Unit tests for the live-output pacing: SendGate, trailing-edge throttle and flood control handling."""
import asyncio
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SendRichMessageDraft

from app.bot import service_message
from app.bot.service_message import DraftStream, SendGate


def _message():
    return SimpleNamespace(chat=SimpleNamespace(id=1), bot=object(), message_id=10)


def _retry_after(seconds):
    return TelegramRetryAfter(
        method=SendRichMessageDraft(chat_id=1, draft_id=1, rich_message={'markdown': ''}),
        message=f'Too Many Requests: retry after {seconds}', retry_after=seconds,
    )


class _DraftRecorder:
    """Stands in for rich_messages.send_rich_draft; records successful calls, raises queued failures first."""

    def __init__(self):
        self.calls = []
        self.failures = []

    async def __call__(self, bot, chat_id, draft_id, markdown, can_stop=True):
        if self.failures:
            raise self.failures.pop(0)
        self.calls.append(markdown)
        return True


@pytest.fixture
def drafts(monkeypatch):
    recorder = _DraftRecorder()
    monkeypatch.setattr(service_message, 'send_rich_draft', recorder)
    return recorder


class TestSendGate:

    def test_first_send_is_immediate_then_spaced(self):
        gate = SendGate(min_interval=10)
        assert gate.delay() == 0
        gate.mark_sent()
        assert 9 < gate.delay() <= 10

    def test_block_extends_the_hold_off(self):
        gate = SendGate(min_interval=0)
        gate.block(5)
        assert 4 < gate.delay() <= 5
        gate.block(1)  # a shorter block never shortens an existing one
        assert 4 < gate.delay() <= 5


class TestTrailingThrottle:

    async def test_burst_is_collapsed_to_the_latest_state(self, drafts):
        stream = DraftStream(_message(), draft_id=7, gate=SendGate(0.2))
        await stream.set_hint('Running a...')
        await stream.set_content('b' * 60)
        await stream.set_hint('Running c...')
        assert drafts.calls == ['<tg-thinking>Running a...</tg-thinking>']  # only the first goes out at once

        await asyncio.sleep(0.35)
        assert drafts.calls[-1] == '<tg-thinking>Running c...</tg-thinking>'  # the latest state, nothing dropped
        assert len(drafts.calls) == 2
        await stream.clear()

    async def test_gate_is_shared_across_phases(self, drafts):
        gate = SendGate(0.2)
        first = DraftStream(_message(), draft_id=1, gate=gate)
        second = DraftStream(_message(), draft_id=2, gate=gate)
        await first.set_hint('phase one')
        await second.set_hint('phase two')
        assert drafts.calls == ['<tg-thinking>phase one</tg-thinking>']
        await asyncio.sleep(0.35)
        assert drafts.calls[-1] == '<tg-thinking>phase two</tg-thinking>'
        await first.clear()
        await second.clear()

    async def test_nothing_lands_after_clear(self, drafts):
        stream = DraftStream(_message(), draft_id=7, gate=SendGate(0.2))
        await stream.set_hint('a')
        await stream.set_hint('b')
        await stream.clear()
        await asyncio.sleep(0.35)
        assert drafts.calls == ['<tg-thinking>a</tg-thinking>']

    async def test_flood_control_keeps_the_text_pending_instead_of_failing(self, drafts):
        drafts.failures.append(_retry_after(0))
        stream = DraftStream(_message(), draft_id=7, gate=SendGate(0.05))
        await stream.set_content('x' * 60)
        assert drafts.calls == [] and stream.failed is False
        await asyncio.sleep(0.3)
        assert drafts.calls == ['x' * 60]  # re-sent once the hold-off passed
        await stream.clear()
