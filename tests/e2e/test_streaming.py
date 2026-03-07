import asyncio

import pytest

from app.openai_helpers.llm_client_factory import LLMClientFactory
from tests.helpers.mock_llm_client import MockLLMClient
from tests.helpers.telegram_factory import make_text_message
from tests.helpers.bot_spy import BotSpy


class TestStreaming:

    async def test_streaming_sends_and_edits_message(self, bot_app):
        """Streaming mode sends initial message then edits it with accumulated content."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)

        user_id = 55555

        # Create user
        mock_llm = MockLLMClient()
        mock_llm.add_response("Hello!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Hi', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.1)

        # Enable streaming
        user = await telegram_bot.db.get_user(user_id)
        user.streaming_answers = True
        await telegram_bot.db.update_user(user)

        # Streaming response
        mock_llm2 = MockLLMClient()
        mock_llm2.add_streaming_response(
            content_chunks=["Hello ", "world, ", "this is ", "a streaming ", "response from the bot!"]
        )
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm2

        update2 = make_text_message('Tell me something', user_id=user_id)
        await dp.process_update(update2)
        await asyncio.sleep(0.3)

        # Streaming should produce both sendMessage and editMessageText calls
        sent = spy.get_sent_messages()
        edited = spy.get_edited_messages()
        # First sendMessage is from initial "Hi" response; second from streaming
        assert len(sent) >= 2, f"Expected at least 2 sendMessage calls, got {len(sent)}"
        assert len(edited) > 0, "Expected at least one editMessageText (streaming update)"

        # Final content should contain the streamed response
        all_texts = spy.get_all_sent_texts() + spy.get_all_edited_texts()
        assert any("streaming" in t for t in all_texts)

    async def test_streaming_with_thinking_blocks(self, bot_app):
        """Streaming with <think> blocks shows thinking emoji, then final content."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)

        user_id = 55556

        # Create user
        mock_llm = MockLLMClient()
        mock_llm.add_response("Hello!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Hi', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.1)

        # Enable streaming
        user = await telegram_bot.db.get_user(user_id)
        user.streaming_answers = True
        await telegram_bot.db.update_user(user)

        # Streaming response with thinking blocks
        mock_llm2 = MockLLMClient()
        mock_llm2.add_streaming_response(
            content_chunks=[
                "<think>",
                "reasoning about",
                " the answer</think>",
                "The actual ",
                "response content here!",
            ]
        )
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm2

        update2 = make_text_message('Think about this', user_id=user_id)
        await dp.process_update(update2)
        await asyncio.sleep(0.3)

        all_texts = spy.get_all_sent_texts() + spy.get_all_edited_texts()
        # Should have shown thinking emoji at some point
        assert any('\U0001f9e0' in t for t in all_texts), \
            f"Expected thinking emoji in messages, got: {all_texts}"
        # Final message should have actual response
        assert any("response content" in t for t in all_texts), \
            f"Expected 'response content' in messages, got: {all_texts}"
