import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import types

from app.openai_helpers.llm_client_factory import LLMClientFactory
from tests.helpers.mock_llm_client import MockLLMClient
from tests.helpers.telegram_factory import make_text_message, make_video_note_message
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


async def _create_user(telegram_bot, dp, user_id, voice_as_prompt):
    mock_llm = MockLLMClient()
    mock_llm.add_response("Hello!")
    LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

    update = make_text_message('Hi', user_id=user_id)
    await dp.process_update(update)
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
            await dp.process_update(update)
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
            await dp.process_update(update)
            await asyncio.sleep(0.3)

        # transcription still echoed to the user
        spy.assert_sent_text_contains('context circle text')
        # LLM was NOT called
        assert len(mock_llm.calls) == 0
