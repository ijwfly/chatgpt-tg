import asyncio

import pytest

from app.openai_helpers.llm_client_factory import LLMClientFactory
from tests.helpers.mock_llm_client import MockLLMClient
from tests.helpers.telegram_factory import make_forward_message, make_text_message
from tests.helpers.bot_spy import BotSpy


class TestForwardedMessages:

    async def test_forwarded_message_as_context(self, bot_app):
        """Forwarded message content and sender name are included in LLM context."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)

        user_id = 88880

        # Create user first
        mock_llm = MockLLMClient()
        mock_llm.add_response("Hello!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Hi', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.1)

        # Send forwarded message as context, then a prompt
        mock_llm2 = MockLLMClient()
        mock_llm2.add_response("Here is a summary of the forwarded message.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm2

        # Forward message (context only, not a prompt since forward_as_prompt defaults to False)
        fwd_update = make_forward_message(
            text='forwarded content from John',
            forward_sender_name='John',
            user_id=user_id,
        )
        # Regular prompt message
        prompt_update = make_text_message('Summarize the forwarded message', user_id=user_id)

        await dp.process_update(fwd_update)
        await asyncio.sleep(0.05)
        await dp.process_update(prompt_update)
        await asyncio.sleep(0.2)

        # LLM should receive context containing the forwarded content and sender
        assert len(mock_llm2.calls) == 1
        messages = mock_llm2.calls[0]['messages']
        all_content = ' '.join(str(m.get('content', '')) for m in messages)
        assert 'John' in all_content, \
            f"Expected 'John' in LLM context, got: {all_content}"
        assert 'forwarded content' in all_content, \
            f"Expected 'forwarded content' in LLM context, got: {all_content}"
