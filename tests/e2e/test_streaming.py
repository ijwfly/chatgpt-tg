import asyncio
import json

import pytest

from app.openai_helpers.llm_client_factory import LLMClientFactory
from tests.helpers.mock_llm_client import MockLLMClient
from tests.helpers.telegram_factory import make_callback_query, make_text_message
from tests.helpers.bot_spy import BotSpy


class TestStreaming:

    async def test_streaming_sends_and_edits_message(self, bot_app):
        """Streaming mode sends a message, then edits it with accumulated content (default: no drafts)."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)

        user_id = 55555

        # Create user
        mock_llm = MockLLMClient()
        mock_llm.add_response("Hello!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Hi', user_id=user_id)
        await dp.feed_update(mock_bot, update)
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
        await dp.feed_update(mock_bot, update2)
        await asyncio.sleep(0.3)

        # Default mode: a rich message is sent, then edited with accumulated content (rich_message payloads)
        sent = spy.get_sent_messages()
        edited = spy.get_edited_messages()
        # First send is from the initial "Hi" response; second is the streamed answer
        assert len(sent) >= 2, f"Expected at least 2 sent messages, got {len(sent)}"
        assert len(edited) > 0, "Expected at least one editMessageText (streaming update)"
        assert not spy.get_drafts(), "Draft streaming is off by default"

        # Final content should contain the streamed response
        assert any("streaming" in t for t in spy.get_all_sent_texts() + spy.get_all_edited_texts())

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
        await dp.feed_update(mock_bot, update)
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
        await dp.feed_update(mock_bot, update2)
        await asyncio.sleep(0.3)

        all_texts = spy.get_all_shown_texts()
        # Should have shown thinking emoji at some point
        assert any('\U0001f9e0' in t for t in all_texts), \
            f"Expected thinking emoji in messages, got: {all_texts}"
        # Final message should have actual response
        assert any("response content" in t for t in all_texts), \
            f"Expected 'response content' in messages, got: {all_texts}"

    async def test_streaming_tool_call_executes(self, bot_app):
        """Streaming tool call is not lost on final usage-only chunk."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)

        user_id = 55557

        # Create user
        mock_llm = MockLLMClient()
        mock_llm.add_response("Hello!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Hi', user_id=user_id)
        await dp.feed_update(mock_bot, update)
        await asyncio.sleep(0.1)

        # Enable streaming + functions
        user = await telegram_bot.db.get_user(user_id)
        user.streaming_answers = True
        user.use_functions = True
        user.system_prompt_settings_enabled = True
        await telegram_bot.db.update_user(user)

        # Streaming response: tool call without content, then final answer
        mock_llm2 = MockLLMClient()
        mock_llm2.add_streaming_response(
            content_chunks=[],
            tool_calls=[{
                'id': 'call_stream_1',
                'function': {
                    'name': 'save_user_settings',
                    'arguments': json.dumps({'settings_text': 'Name: StreamTest'}),
                },
            }],
        )
        mock_llm2.add_streaming_response(content_chunks=["Settings saved!"])
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm2

        update2 = make_text_message('Save my name as StreamTest', user_id=user_id)
        await dp.feed_update(mock_bot, update2)
        await asyncio.sleep(0.3)

        spy.assert_sent_text_contains("Settings saved!")

        # Verify DB was updated by the function
        user = await telegram_bot.db.get_user(user_id)
        assert user.system_prompt_settings == 'Name: StreamTest'


class TestStreamingCancellation:

    async def test_stop_after_the_tool_call_chunk_does_not_execute_the_tool(self, bot_app):
        """Stop pressed while the tool call is still streaming: the call is dropped instead of executed."""
        telegram_bot, dp, mock_bot = bot_app
        user_id = 55560

        mock_llm = MockLLMClient()
        mock_llm.add_response("Hello!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm
        await dp.feed_update(mock_bot, make_text_message('Hi', user_id=user_id))
        await asyncio.sleep(0.1)

        user = await telegram_bot.db.get_user(user_id)
        user.streaming_answers = True
        user.use_functions = True
        user.system_prompt_settings_enabled = True
        await telegram_bot.db.update_user(user)

        mock_llm2 = MockLLMClient()
        mock_llm2.add_streaming_response(
            content_chunks=[],
            tool_calls=[{
                'id': 'call_cut_1',
                'function': {'name': 'save_user_settings', 'arguments': json.dumps({'settings_text': 'Name: Cut'})},
            }],
            chunk_delay=0.3,
        )
        mock_llm2.add_streaming_response(content_chunks=["never requested"])
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm2

        turn = asyncio.create_task(dp.feed_update(mock_bot, make_text_message('Save my name', user_id=user_id)))
        await asyncio.sleep(0.45)  # the tool-call chunk is out, the final usage chunk is not
        await dp.feed_update(mock_bot, make_callback_query('cancel.cancel', message_id=1, user_id=user_id))
        await asyncio.wait_for(turn, timeout=3)

        user = await telegram_bot.db.get_user(user_id)
        assert user.system_prompt_settings != 'Name: Cut'
        assert len(mock_llm2.calls) == 1
