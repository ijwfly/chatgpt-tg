import asyncio
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import types
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from app.openai_helpers.llm_client_factory import LLMClientFactory
from tests.helpers.mock_llm_client import MockLLMClient
from tests.helpers.telegram_factory import make_text_message, make_video_note_message, make_voice_message
from tests.helpers.bot_spy import BotSpy


class _FakeAudio:
    """Minimal stand-in for pydub.AudioSegment (ms length + export)."""
    def __len__(self):
        return 3000  # 3 seconds

    def export(self, *args, **kwargs):
        return None


def _mock_video_note_download(mock_bot):
    mock_bot.get_file = AsyncMock(return_value=types.File(
        file_id='test-video-note-id',
        file_unique_id='unique-test-video-note-id',
        file_size=2048,
        file_path='video_notes/test-note',
    ))

    async def fake_download(file_path, destination=None, **kwargs):
        with open(destination, 'wb') as f:
            f.write(b'fake-mp4-bytes')

    mock_bot.download_file = AsyncMock(side_effect=fake_download)


@contextmanager
def _patched_transcription(text='transcribed voice text'):
    """Makes the voice path runnable without ffmpeg/Whisper."""
    with patch('app.bot.batched_input_handler.AudioSegment') as mock_audioseg, \
            patch('app.bot.batched_input_handler.get_audio_speech_to_text', AsyncMock(return_value=text)):
        mock_audioseg.from_file.return_value = _FakeAudio()
        yield


async def _create_user(telegram_bot, dp, user_id, voice_as_prompt):
    mock_llm = MockLLMClient()
    mock_llm.add_response("Hello!")
    LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

    update = make_text_message('Hi', user_id=user_id)
    await dp.feed_update(telegram_bot.bot, update)
    await asyncio.sleep(0.1)

    user = await telegram_bot.db.get_user(user_id)
    user.voice_as_prompt = voice_as_prompt
    await telegram_bot.db.update_user(user)
    return user


class TestVideoNoteTranscription:

    async def test_video_note_as_prompt_triggers_llm(self, bot_app):
        """With voice_as_prompt on, a video note is transcribed and answered by the LLM."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 90001

        await _create_user(telegram_bot, dp, user_id, voice_as_prompt=True)
        _mock_video_note_download(mock_bot)

        mock_llm = MockLLMClient()
        mock_llm.add_response("Answering your circle video.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        with patch('app.bot.batched_input_handler.AudioSegment') as mock_audioseg, \
                patch('app.bot.batched_input_handler.get_audio_speech_to_text',
                      AsyncMock(return_value='transcribed circle text')):
            mock_audioseg.from_file.return_value = _FakeAudio()
            update = make_video_note_message(user_id=user_id)
            await dp.feed_update(mock_bot, update)
            await asyncio.sleep(0.3)

        # transcription echoed back to the user
        spy.assert_sent_text_contains('transcribed circle text')
        # LLM was called and its answer delivered
        spy.assert_sent_text_contains('Answering your circle video.')
        assert len(mock_llm.calls) == 1
        all_contents = [str(m.get('content', '')) for m in mock_llm.calls[0]['messages']]
        assert any('transcribed circle text' in c for c in all_contents)

        # whisper usage recorded (table is truncated between tests)
        usage = await telegram_bot.db.connection_pool.fetchval(
            'SELECT count(*) FROM chatgpttg.whisper_usage'
        )
        assert usage == 1

    async def test_video_note_as_context_only(self, bot_app):
        """With voice_as_prompt off, a video note is transcribed into context without calling the LLM."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 90002

        await _create_user(telegram_bot, dp, user_id, voice_as_prompt=False)
        _mock_video_note_download(mock_bot)

        mock_llm = MockLLMClient()
        # No response queued: if the LLM were called, it would raise
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        with patch('app.bot.batched_input_handler.AudioSegment') as mock_audioseg, \
                patch('app.bot.batched_input_handler.get_audio_speech_to_text',
                      AsyncMock(return_value='context circle text')):
            mock_audioseg.from_file.return_value = _FakeAudio()
            update = make_video_note_message(user_id=user_id)
            await dp.feed_update(mock_bot, update)
            await asyncio.sleep(0.3)

        # transcription still echoed to the user
        spy.assert_sent_text_contains('context circle text')
        # LLM was NOT called
        assert len(mock_llm.calls) == 0


class TestVoiceReplyBranching:
    """A reply to the user's own voice message must resolve to its transcription."""

    async def test_reply_to_own_voice_resolves_to_transcription(self, bot_app):
        telegram_bot, dp, mock_bot = bot_app
        user_id = 90010

        await _create_user(telegram_bot, dp, user_id, voice_as_prompt=False)
        _mock_video_note_download(mock_bot)

        voice_update = make_voice_message(user_id=user_id)
        with _patched_transcription('my recorded question'):
            await dp.feed_update(mock_bot, voice_update)
            await asyncio.sleep(0.3)

        row = await telegram_bot.db.get_telegram_message(user_id, voice_update.message.message_id)
        assert row is not None, 'voice message must resolve to its transcription row'
        assert 'my recorded question' in str(row.message.content)

    async def test_reply_to_own_voice_keeps_branch(self, bot_app):
        telegram_bot, dp, mock_bot = bot_app
        user_id = 90011

        await _create_user(telegram_bot, dp, user_id, voice_as_prompt=False)
        _mock_video_note_download(mock_bot)

        mock_llm = MockLLMClient()
        mock_llm.add_response("Sure.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm
        await dp.feed_update(mock_bot, make_text_message('Remember the number 42', user_id=user_id))
        await asyncio.sleep(0.2)

        voice_update = make_voice_message(user_id=user_id)
        with _patched_transcription('what number did I ask about'):
            await dp.feed_update(mock_bot, voice_update)
            await asyncio.sleep(0.3)

        mock_llm2 = MockLLMClient()
        mock_llm2.add_response("Ok.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm2
        await dp.feed_update(mock_bot, make_text_message('Unrelated later message', user_id=user_id))
        await asyncio.sleep(0.2)

        mock_llm3 = MockLLMClient()
        mock_llm3.add_response("42.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm3
        await dp.feed_update(mock_bot, make_text_message(
            'Answer my voice question', user_id=user_id,
            reply_to_message_id=voice_update.message.message_id,
        ))
        await asyncio.sleep(0.3)

        contents = [str(m.get('content', '')) for m in mock_llm3.calls[0]['messages']]
        assert any('Remember the number 42' in c for c in contents)
        assert any('what number did I ask about' in c for c in contents)
        assert not any('Unrelated later message' in c for c in contents)

    async def test_multichunk_voice_alias_points_to_last_chunk(self, bot_app):
        """With several transcription chunks the alias must lead to the last one (full branch)."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 90012

        await _create_user(telegram_bot, dp, user_id, voice_as_prompt=False)
        _mock_video_note_download(mock_bot)

        voice_update = make_voice_message(user_id=user_id)
        with _patched_transcription('A' * 4080 + 'TAIL_MARKER'):
            await dp.feed_update(mock_bot, voice_update)
            await asyncio.sleep(0.3)

        echoed = [m for m in spy.get_sent_messages()
                  if (m.get('reply_parameters') or {}).get('message_id') == voice_update.message.message_id]
        assert len(echoed) == 2, f'transcription must be echoed in two chunks, got {len(echoed)}'

        row = await telegram_bot.db.get_telegram_message(user_id, voice_update.message.message_id)
        assert row is not None
        assert 'TAIL_MARKER' in str(row.message.content)

        previous = await telegram_bot.db.get_messages_by_ids(row.previous_message_ids)
        assert any('speech2text:' in str(m.message.content) for m in previous), \
            'branch of the aliased row must contain the first chunk'

    async def test_voice_echo_failure_keeps_transcription(self, bot_app):
        """If the transcription echo cannot be sent, the transcription still reaches the context."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 90013

        user = await _create_user(telegram_bot, dp, user_id, voice_as_prompt=False)
        _mock_video_note_download(mock_bot)

        with _patched_transcription('lost echo transcription'), \
                patch.object(types.Message, 'reply', AsyncMock(side_effect=TelegramBadRequest(method=SendMessage(chat_id=0, text=''), message='blocked'))):
            await dp.feed_update(mock_bot, make_voice_message(user_id=user_id))
            await asyncio.sleep(0.3)

        last = await telegram_bot.db.get_last_message(user.id, user_id)
        assert 'lost echo transcription' in str(last.message.content)
        assert last.tg_message_id == -1
        assert not any('Something went wrong' in t for t in spy.get_all_sent_texts())

    async def test_reply_to_own_video_note_resolves_to_transcription(self, bot_app):
        telegram_bot, dp, mock_bot = bot_app
        user_id = 90014

        await _create_user(telegram_bot, dp, user_id, voice_as_prompt=False)
        _mock_video_note_download(mock_bot)

        note_update = make_video_note_message(user_id=user_id)
        with _patched_transcription('round video text'):
            await dp.feed_update(mock_bot, note_update)
            await asyncio.sleep(0.3)

        row = await telegram_bot.db.get_telegram_message(user_id, note_update.message.message_id)
        assert row is not None
        assert 'round video text' in str(row.message.content)
