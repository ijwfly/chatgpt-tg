"""Telegram Rich Messages: LLM answers go out as `sendRichMessage(markdown)`, streaming uses drafts."""
import asyncio
import json

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendRichMessage

import settings

from app.openai_helpers.llm_client_factory import LLMClientFactory
from tests.helpers.bot_spy import BotSpy
from tests.helpers.mock_llm_client import MockLLMClient
from tests.helpers.telegram_factory import make_text_message


async def _create_user(telegram_bot, dp, user_id, **fields):
    mock_llm = MockLLMClient()
    mock_llm.add_response('Hello!')
    LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm
    await dp.feed_update(telegram_bot.bot, make_text_message('Hi', user_id=user_id))
    await asyncio.sleep(0.1)
    user = await telegram_bot.db.get_user(user_id)
    for key, value in fields.items():
        setattr(user, key, value)
    await telegram_bot.db.update_user(user)
    return user


@pytest.fixture
def draft_streaming():
    """Draft streaming is off by default (settings.RICH_DRAFT_STREAMING); these tests turn it on."""
    old = settings.RICH_DRAFT_STREAMING
    settings.RICH_DRAFT_STREAMING = True
    yield
    settings.RICH_DRAFT_STREAMING = old


def _parse_error():
    return TelegramBadRequest(method=SendRichMessage(chat_id=0, rich_message={'markdown': ''}),
                              message="Bad Request: can't parse rich message: unclosed tag")


class TestRichFinalAnswer:

    async def test_final_answer_is_sent_as_rich_markdown(self, bot_app):
        """The LLM answer goes out verbatim as InputRichMessage.markdown, without any parse_mode."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 71001
        await _create_user(telegram_bot, dp, user_id)

        answer = '# Title\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```python\nprint(1)\n```'
        mock_llm = MockLLMClient()
        mock_llm.add_response(answer)
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        await dp.feed_update(mock_bot, make_text_message('Give me a table', user_id=user_id))
        await asyncio.sleep(0.2)

        rich = spy.get_rich_messages()
        assert rich and rich[-1]['rich_message'] == {'markdown': answer}
        assert 'parse_mode' not in rich[-1]
        # the answer is stored in the dialog under the rich message's telegram id
        rich_message_id = spy.get_last_message_id_for_method('sendRichMessage')
        row = await telegram_bot.db.get_telegram_message(user_id, rich_message_id)
        assert row is not None and row.message.content == answer

    async def test_rich_rejected_falls_back_to_plain_text(self, bot_app):
        """If Telegram can't parse the markup the same text is re-sent as a plain sendMessage."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 71002
        await _create_user(telegram_bot, dp, user_id)

        mock_llm = MockLLMClient()
        mock_llm.add_response('Broken <details> markup here')
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm
        mock_bot.session.fail_next('sendRichMessage', _parse_error())

        await dp.feed_update(mock_bot, make_text_message('Break it', user_id=user_id))
        await asyncio.sleep(0.2)

        plain = [m for m in spy.get_plain_messages() if 'Broken <details>' in m.get('text', '')]
        assert len(plain) == 1 and 'parse_mode' not in plain[0]
        assert not any('Something went wrong' in t for t in spy.get_all_sent_texts())

    async def test_reply_branch_uses_reply_parameters(self, bot_app):
        """Answering inside a sub-dialogue replies to the user's message, as the plain path did."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 71003
        await _create_user(telegram_bot, dp, user_id)
        first_answer_id = spy.get_last_message_id_for_method('sendRichMessage')

        mock_llm = MockLLMClient()
        mock_llm.add_response('Reply answer')
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Follow-up', user_id=user_id, reply_to_message_id=first_answer_id)
        await dp.feed_update(mock_bot, update)
        await asyncio.sleep(0.2)

        rich = spy.get_rich_messages()[-1]
        assert rich['rich_message']['markdown'] == 'Reply answer'
        assert rich['reply_parameters']['message_id'] == update.message.message_id


def _draft_error():
    from aiogram.methods import SendRichMessageDraft
    return TelegramBadRequest(method=SendRichMessageDraft(chat_id=0, draft_id=1, rich_message={'markdown': ''}),
                              message='Bad Request: DRAFT_NOT_SUPPORTED')


class TestDraftStreaming:

    async def test_private_chat_streams_drafts_then_sends_one_rich_message(self, bot_app, draft_streaming):
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 71010
        await _create_user(telegram_bot, dp, user_id, streaming_answers=True)
        calls_before = len(spy.get_all_calls())

        mock_llm = MockLLMClient()
        mock_llm.add_streaming_response(
            content_chunks=['Streamed ', 'answer that ', 'is long enough ', 'to be drafted, ', 'finally done.'],
        )
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Stream', user_id=user_id)
        await dp.feed_update(mock_bot, update)
        await asyncio.sleep(0.3)

        drafts = spy.get_drafts()
        assert drafts, 'expected sendRichMessageDraft calls in a private chat'
        assert {d['draft_id'] for d in drafts} == {update.message.message_id * 100 + 1}
        assert all(d['can_stop'] is True for d in drafts)
        assert all(d['chat_id'] == user_id for d in drafts)
        assert any('Streamed answer' in t for t in spy.get_all_draft_texts())

        turn_calls = [m for m, _ in spy.get_all_calls()[calls_before:]]
        assert turn_calls.count('sendRichMessage') == 1
        assert 'editMessageText' not in turn_calls and 'deleteMessage' not in turn_calls and 'sendMessage' not in turn_calls
        assert spy.get_rich_messages()[-1]['rich_message']['markdown'] == \
            'Streamed answer that is long enough to be drafted, finally done.'
        assert 'reply_markup' not in spy.get_rich_messages()[-1]

    async def test_thinking_and_tool_hint_are_rendered_as_tg_thinking(self, bot_app, draft_streaming):
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 71011
        await _create_user(
            telegram_bot, dp, user_id, streaming_answers=True, use_functions=True, system_prompt_settings_enabled=True,
        )

        mock_llm = MockLLMClient()
        mock_llm.add_streaming_response(
            content_chunks=['<think>', 'reasoning about the question', '</think>'],
            tool_calls=[{
                'id': 'call_1',
                'function': {'name': 'save_user_settings', 'arguments': json.dumps({'settings_text': 'Name: Draft'})},
            }],
        )
        mock_llm.add_streaming_response(content_chunks=['Saved, this answer is long ', 'enough to be shown.'])
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        await dp.feed_update(mock_bot, make_text_message('Remember me', user_id=user_id))
        await asyncio.sleep(0.4)

        drafts = spy.get_all_draft_texts()
        assert any(t.startswith('<tg-thinking>\U0001f9e0') and t.endswith('</tg-thinking>') for t in drafts), drafts
        assert '<tg-thinking>Saving user info...</tg-thinking>' in drafts
        assert not any('<tg-thinking>' in t for t in spy.get_all_sent_texts())
        spy.assert_sent_text_contains('Saved, this answer')
        assert (await telegram_bot.db.get_user(user_id)).system_prompt_settings == 'Name: Draft'

    async def test_private_chat_edits_a_service_message_by_default(self, bot_app):
        """With RICH_DRAFT_STREAMING off (the default) private chats stream by editing a rich message."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 71015
        await _create_user(telegram_bot, dp, user_id, streaming_answers=True)
        calls_before = len(spy.get_all_calls())

        mock_llm = MockLLMClient()
        mock_llm.add_streaming_response(
            content_chunks=['Default answer that ', 'is long enough to ', 'trigger streaming ', 'edits, done.'],
        )
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        await dp.feed_update(mock_bot, make_text_message('Stream', user_id=user_id))
        await asyncio.sleep(0.3)

        turn = spy.get_all_calls()[calls_before:]
        methods = [m for m, _ in turn]
        assert 'sendRichMessageDraft' not in methods
        first = next(d for m, d in turn if m == 'sendRichMessage')
        assert first['reply_markup']['inline_keyboard'][0][0]['callback_data'] == 'cancel.cancel'
        edits = [d for m, d in turn if m == 'editMessageText']
        assert edits and edits[-1]['rich_message']['markdown'].endswith('edits, done.')
        assert 'reply_markup' not in edits[-1]
        assert 'deleteMessage' not in methods

    async def test_group_chat_edits_a_service_message_with_stop_button(self, bot_app, draft_streaming):
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 71012
        group_id = -100710120
        mock_llm = MockLLMClient()
        mock_llm.add_response('Hello!')
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm
        await dp.feed_update(mock_bot, make_text_message('Hi', user_id=user_id, chat_id=group_id, chat_type='supergroup'))
        await asyncio.sleep(0.1)
        user = await telegram_bot.db.get_user(user_id)
        user.streaming_answers = True
        await telegram_bot.db.update_user(user)
        calls_before = len(spy.get_all_calls())

        mock_llm = MockLLMClient()
        mock_llm.add_streaming_response(
            content_chunks=['Group answer that ', 'is long enough to ', 'trigger streaming ', 'edits in the group.'],
        )
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        await dp.feed_update(mock_bot, make_text_message('Stream', user_id=user_id, chat_id=group_id, chat_type='supergroup'))
        await asyncio.sleep(0.3)

        turn = spy.get_all_calls()[calls_before:]
        assert not any(m == 'sendRichMessageDraft' for m, _ in turn)
        first = next(d for m, d in turn if m == 'sendRichMessage')
        assert first['chat_id'] == group_id
        assert first['reply_markup']['inline_keyboard'][0][0]['callback_data'] == 'cancel.cancel'
        edits = [d for m, d in turn if m == 'editMessageText']
        assert edits and all('rich_message' in d for d in edits)
        assert edits[-1]['rich_message']['markdown'].endswith('edits in the group.')
        assert 'reply_markup' not in edits[-1]  # the finished answer has no Stop button
        assert not any(m == 'deleteMessage' for m, _ in turn)

    async def test_draft_failure_falls_back_to_service_message(self, bot_app, draft_streaming):
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 71013
        await _create_user(telegram_bot, dp, user_id, streaming_answers=True)
        mock_bot.session.fail_next('sendRichMessageDraft', _draft_error())
        calls_before = len(spy.get_all_calls())

        mock_llm = MockLLMClient()
        mock_llm.add_streaming_response(
            content_chunks=['Fallback answer that ', 'is long enough to ', 'trigger streaming ', 'updates, done.'],
        )
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        await dp.feed_update(mock_bot, make_text_message('Stream', user_id=user_id))
        await asyncio.sleep(0.3)

        turn = spy.get_all_calls()[calls_before:]
        methods = [m for m, _ in turn]
        assert methods.count('sendRichMessageDraft') == 1  # the rejected one
        assert 'sendRichMessage' in methods and 'editMessageText' in methods
        assert 'deleteMessage' not in methods
        spy.assert_sent_text_contains('updates, done.')

    async def test_agent_phases_use_distinct_draft_ids(self, bot_app, draft_streaming):
        """A tool round-trip that produces text twice (agent phases) streams each phase under its own draft id."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 71014
        await _create_user(
            telegram_bot, dp, user_id, streaming_answers=True, use_functions=True, system_prompt_settings_enabled=True,
        )

        mock_llm = MockLLMClient()
        mock_llm.add_streaming_response(
            content_chunks=['First phase text that is long enough ', 'to be streamed before the tool call.'],
            tool_calls=[{
                'id': 'call_2',
                'function': {'name': 'save_user_settings', 'arguments': json.dumps({'settings_text': 'Name: Phase'})},
            }],
        )
        mock_llm.add_streaming_response(content_chunks=['Second phase text that is long enough ', 'to be streamed too.'])
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Two phases', user_id=user_id)
        await dp.feed_update(mock_bot, update)
        await asyncio.sleep(0.4)

        draft_ids = [d['draft_id'] for d in spy.get_drafts()]
        base = update.message.message_id * 100
        assert set(draft_ids) >= {base + 1, base + 2}, draft_ids


class TestNativeStop:

    async def test_stopped_generation_update_cancels_the_streaming_turn(self, bot_app, draft_streaming):
        """The native Stop button (Bot API 10.3 `stopped_message_generation`) cancels the user's turn."""
        import warnings
        from tests.helpers.telegram_factory import make_stopped_generation_update

        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 71020
        await _create_user(telegram_bot, dp, user_id, streaming_answers=True)

        chunks = [f'chunk {i} of a slow answer that keeps going, ' for i in range(40)]
        mock_llm = MockLLMClient()
        mock_llm.add_streaming_response(content_chunks=chunks, chunk_delay=0.02)
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        # feed_update awaits the whole turn, so the turn runs as a task and the stop lands mid-stream
        turn = asyncio.create_task(dp.feed_update(mock_bot, make_text_message('Slow stream', user_id=user_id)))
        await asyncio.sleep(0.25)
        drafts = spy.get_drafts()
        assert drafts and drafts[-1]['can_stop'] is True

        with warnings.catch_warnings():
            # aiogram 3.30 warns about unknown update types; the middleware must swallow the update first
            warnings.simplefilter('error', RuntimeWarning)
            await dp.feed_update(mock_bot, make_stopped_generation_update(user_id, drafts[-1]['draft_id']))
        await asyncio.wait_for(turn, timeout=2)

        final = spy.get_rich_messages()[-1]['rich_message']['markdown']
        assert 'chunk 0' in final and 'chunk 39' not in final, final
        # the finished (partial) answer is a real message, so the user keeps what was generated
        assert spy.get_rich_messages()[-1]['chat_id'] == user_id

    async def test_stopped_generation_for_idle_user_is_ignored(self, bot_app):
        from tests.helpers.telegram_factory import make_stopped_generation_update

        telegram_bot, dp, mock_bot = bot_app
        calls_before = len(mock_bot.session.requests)
        await dp.feed_update(mock_bot, make_stopped_generation_update(71021, 5))
        assert len(mock_bot.session.requests) == calls_before
        assert '71021' not in telegram_bot.cancellation_manager._cancellation_tokens

    def test_polling_requests_the_stopped_generation_update(self, bot_app):
        telegram_bot, dp, mock_bot = bot_app
        from app.bot.cancellation_manager import STOPPED_GENERATION_UPDATE
        allowed = dp.resolve_used_update_types() + [STOPPED_GENERATION_UPDATE]
        assert 'message' in allowed and 'callback_query' in allowed and STOPPED_GENERATION_UPDATE in allowed
