"""Telegram Rich Messages: LLM answers go out as `sendRichMessage(markdown)`, streaming uses drafts."""
import asyncio

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendRichMessage

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
