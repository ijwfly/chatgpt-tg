import asyncio

import pytest

from app.openai_helpers.llm_client_factory import LLMClientFactory
from tests.helpers.mock_llm_client import MockLLMClient
from tests.helpers.mock_llm_client import MockLLMClient
from tests.helpers.telegram_factory import make_text_message
from tests.helpers.bot_spy import BotSpy


class TestErrorHandling:

    async def test_error_on_no_llm_response(self, bot_app):
        """Empty response queue in MockLLMClient triggers error message to user."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)

        user_id = 99990

        # Inject mock with no responses
        mock_llm = MockLLMClient()
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Hello', user_id=user_id)
        with pytest.raises(ValueError):
            await dp.process_update(update)
        await asyncio.sleep(0.1)

        spy.assert_sent_text_contains("Something went wrong")
