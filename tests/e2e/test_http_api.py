import asyncio
import json

import jwt
import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import settings
from app.api.http_api import create_api_app, generate_api_token
from app.openai_helpers.llm_client_factory import LLMClientFactory
from tests.helpers.mock_llm_client import MockLLMClient
from tests.helpers.telegram_factory import make_text_message
from tests.helpers.bot_spy import BotSpy


API_SECRET = 'test-secret-for-jwt'


@pytest_asyncio.fixture
async def api_client(bot_app):
    """aiohttp TestClient wrapping the HTTP API app."""
    telegram_bot, dp, mock_bot = bot_app
    old_secret = settings.HTTP_API_SECRET
    settings.HTTP_API_SECRET = API_SECRET
    try:
        api_app = create_api_app(mock_bot, telegram_bot.db)
        async with TestClient(TestServer(api_app)) as client:
            yield client, telegram_bot, dp, mock_bot
    finally:
        settings.HTTP_API_SECRET = old_secret


def _wildcard_token():
    return jwt.encode({"user_id": None}, API_SECRET, algorithm="HS256")


def _user_token(user_id):
    return jwt.encode({"user_id": user_id}, API_SECRET, algorithm="HS256")


async def _create_user(dp, user_id):
    """Send a message through dispatcher to create user in DB."""
    mock_llm = MockLLMClient()
    mock_llm.add_response("Init response")
    LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm
    update = make_text_message('init', user_id=user_id)
    await dp.process_update(update)
    await asyncio.sleep(0.1)


class TestHealthEndpoint:

    async def test_health_no_auth(self, api_client):
        client, *_ = api_client
        resp = await client.get('/api/v1/health')
        assert resp.status == 200
        data = await resp.json()
        assert data['status'] == 'ok'


class TestAuth:

    async def test_missing_auth_header(self, api_client):
        client, *_ = api_client
        resp = await client.post('/api/v1/inject', json={"chat_id": 123, "text": "hi"})
        assert resp.status == 401

    async def test_invalid_token(self, api_client):
        client, *_ = api_client
        resp = await client.post(
            '/api/v1/inject',
            json={"chat_id": 123, "text": "hi"},
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert resp.status == 401

    async def test_wrong_secret(self, api_client):
        client, *_ = api_client
        bad_token = jwt.encode({"user_id": None}, "wrong-secret", algorithm="HS256")
        resp = await client.post(
            '/api/v1/inject',
            json={"chat_id": 123, "text": "hi"},
            headers={"Authorization": f"Bearer {bad_token}"},
        )
        assert resp.status == 401

    async def test_user_token_wrong_chat_id(self, api_client):
        client, telegram_bot, dp, _ = api_client
        user_id = 55501
        await _create_user(dp, user_id)

        token = _user_token(user_id)
        resp = await client.post(
            '/api/v1/inject',
            json={"chat_id": 999999, "text": "hi"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 403

    async def test_user_token_correct_chat_id(self, api_client):
        client, telegram_bot, dp, _ = api_client
        user_id = 55502
        await _create_user(dp, user_id)

        mock_llm = MockLLMClient()
        mock_llm.add_response("API response")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        token = _user_token(user_id)
        resp = await client.post(
            '/api/v1/inject',
            json={"chat_id": user_id, "text": "hello", "wait_for_response": True},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 200

    async def test_wildcard_token_any_chat_id(self, api_client):
        client, telegram_bot, dp, _ = api_client
        user_id = 55503
        await _create_user(dp, user_id)

        mock_llm = MockLLMClient()
        mock_llm.add_response("Wildcard response")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        token = _wildcard_token()
        resp = await client.post(
            '/api/v1/inject',
            json={"chat_id": user_id, "text": "hello", "wait_for_response": True},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 200


class TestValidation:

    async def test_missing_chat_id(self, api_client):
        client, *_ = api_client
        token = _wildcard_token()
        resp = await client.post(
            '/api/v1/inject',
            json={"text": "hi"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 400

    async def test_missing_text_and_images(self, api_client):
        client, *_ = api_client
        token = _wildcard_token()
        resp = await client.post(
            '/api/v1/inject',
            json={"chat_id": 123},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 400

    async def test_invalid_json(self, api_client):
        client, *_ = api_client
        token = _wildcard_token()
        resp = await client.post(
            '/api/v1/inject',
            data="not json",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        assert resp.status == 400

    async def test_user_not_found(self, api_client):
        client, *_ = api_client
        token = _wildcard_token()
        resp = await client.post(
            '/api/v1/inject',
            json={"chat_id": 999888777, "text": "hello", "wait_for_response": True},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 404


class TestInjectEndpoint:

    async def test_echo_message_sent_to_chat(self, api_client):
        """Injected text is echoed to chat with 📨 prefix before the LLM response."""
        client, telegram_bot, dp, mock_bot = api_client
        spy = BotSpy(mock_bot)
        user_id = 55509
        await _create_user(dp, user_id)
        spy.mock_bot.request.reset_mock()

        mock_llm = MockLLMClient()
        mock_llm.add_response("Echo test response")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        token = _wildcard_token()
        await client.post(
            '/api/v1/inject',
            json={"chat_id": user_id, "text": "External event happened", "wait_for_response": True},
            headers={"Authorization": f"Bearer {token}"},
        )

        sent_texts = spy.get_all_sent_texts()
        # First message should be the echo
        echo_msgs = [t for t in sent_texts if '📨' in t]
        assert len(echo_msgs) >= 1
        assert 'External event happened' in echo_msgs[0]
        # Second should be the LLM response
        spy.assert_sent_text_contains("Echo test response")

    async def test_echo_message_with_images(self, api_client):
        """Echo message includes image URLs when images are injected."""
        client, telegram_bot, dp, mock_bot = api_client
        spy = BotSpy(mock_bot)
        user_id = 55508
        await _create_user(dp, user_id)
        spy.mock_bot.request.reset_mock()

        mock_llm = MockLLMClient()
        mock_llm.add_response("Saw image")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        token = _wildcard_token()
        await client.post(
            '/api/v1/inject',
            json={
                "chat_id": user_id,
                "text": "Check this",
                "images": [{"url": "https://example.com/pic.png"}],
                "wait_for_response": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        sent_texts = spy.get_all_sent_texts()
        echo_msgs = [t for t in sent_texts if '📨' in t]
        assert len(echo_msgs) >= 1
        assert 'Check this' in echo_msgs[0]
        assert 'example.com/pic.png' in echo_msgs[0]

    async def test_fire_and_forget(self, api_client):
        client, telegram_bot, dp, mock_bot = api_client
        spy = BotSpy(mock_bot)
        user_id = 55510
        await _create_user(dp, user_id)

        mock_llm = MockLLMClient()
        mock_llm.add_response("Fire and forget response")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        token = _wildcard_token()
        resp = await client.post(
            '/api/v1/inject',
            json={"chat_id": user_id, "text": "background task"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 202
        data = await resp.json()
        assert data['status'] == 'accepted'

        # Wait for background task to complete
        await asyncio.sleep(0.3)
        spy.assert_sent_text_contains("Fire and forget response")

    async def test_wait_for_response(self, api_client):
        client, telegram_bot, dp, mock_bot = api_client
        user_id = 55511
        await _create_user(dp, user_id)

        mock_llm = MockLLMClient()
        mock_llm.add_response("Sync response text")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        token = _wildcard_token()
        resp = await client.post(
            '/api/v1/inject',
            json={"chat_id": user_id, "text": "sync request", "wait_for_response": True},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data['status'] == 'ok'
        assert 'Sync response text' in data['response_text']
        assert len(data['tg_message_ids']) >= 1

    async def test_llm_receives_injected_text(self, api_client):
        client, telegram_bot, dp, mock_bot = api_client
        user_id = 55512
        await _create_user(dp, user_id)

        mock_llm = MockLLMClient()
        mock_llm.add_response("Got it")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        token = _wildcard_token()
        resp = await client.post(
            '/api/v1/inject',
            json={"chat_id": user_id, "text": "Injected message content", "wait_for_response": True},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 200

        assert len(mock_llm.calls) == 1
        messages = mock_llm.calls[0]['messages']
        user_msgs = [m for m in messages if m.get('role') == 'user']
        assert any('Injected message content' in str(m.get('content', '')) for m in user_msgs)

    async def test_response_saved_to_context(self, api_client):
        """Injected message and response are saved — second inject sees conversation history."""
        client, telegram_bot, dp, mock_bot = api_client
        user_id = 55513
        await _create_user(dp, user_id)

        # First inject
        mock_llm = MockLLMClient()
        mock_llm.add_response("First API response")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        token = _wildcard_token()
        await client.post(
            '/api/v1/inject',
            json={"chat_id": user_id, "text": "First message", "wait_for_response": True},
            headers={"Authorization": f"Bearer {token}"},
        )

        # Second inject — should see context from first
        mock_llm2 = MockLLMClient()
        mock_llm2.add_response("Second API response")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm2

        await client.post(
            '/api/v1/inject',
            json={"chat_id": user_id, "text": "Second message", "wait_for_response": True},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert len(mock_llm2.calls) == 1
        messages = mock_llm2.calls[0]['messages']
        all_content = ' '.join(str(m.get('content', '')) for m in messages)
        assert 'First message' in all_content
        assert 'First API response' in all_content

    async def test_inject_with_images(self, api_client):
        client, telegram_bot, dp, mock_bot = api_client
        user_id = 55514
        await _create_user(dp, user_id)

        mock_llm = MockLLMClient()
        mock_llm.add_response("I see the image")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        token = _wildcard_token()
        resp = await client.post(
            '/api/v1/inject',
            json={
                "chat_id": user_id,
                "text": "Look at this",
                "images": [{"url": "https://example.com/image.jpg"}],
                "wait_for_response": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 200

        # Verify LLM received multimodal content
        assert len(mock_llm.calls) == 1
        messages = mock_llm.calls[0]['messages']
        user_msgs = [m for m in messages if m.get('role') == 'user']
        assert len(user_msgs) >= 1
        # Content should be a list (multimodal) containing image_url
        last_user = user_msgs[-1]
        content = last_user.get('content', '')
        assert isinstance(content, list)
        content_types = [part.get('type') for part in content]
        assert 'image_url' in content_types

    async def test_inject_images_only(self, api_client):
        client, telegram_bot, dp, mock_bot = api_client
        user_id = 55515
        await _create_user(dp, user_id)

        mock_llm = MockLLMClient()
        mock_llm.add_response("Image only response")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        token = _wildcard_token()
        resp = await client.post(
            '/api/v1/inject',
            json={
                "chat_id": user_id,
                "images": [{"url": "https://example.com/photo.png"}],
                "wait_for_response": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 200

    async def test_linked_mode_with_reply_to(self, api_client):
        """Inject with reply_to_message_id creates a subdialog context."""
        client, telegram_bot, dp, mock_bot = api_client
        spy = BotSpy(mock_bot)
        user_id = 55516
        await _create_user(dp, user_id)

        # Send first message via Telegram to establish context
        mock_llm = MockLLMClient()
        mock_llm.add_response("Original response")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Original question', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.1)

        # Get the bot response message_id from spy
        sent = spy.get_sent_messages()
        # Find the response message (last sendMessage with the response text)
        response_msgs = [m for m in sent if 'Original response' in m.get('text', '')]
        assert len(response_msgs) > 0

        # The tg_message_id in DB comes from the mock_bot.request return value
        # We need to find the message_id that was stored in DB
        user = await telegram_bot.db.get_user(user_id)
        last_msg = await telegram_bot.db.get_last_message(user.id, user_id)
        reply_to_id = last_msg.tg_message_id

        # Now inject via API with reply_to_message_id
        mock_llm2 = MockLLMClient()
        mock_llm2.add_response("Subdialog reply")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm2

        token = _wildcard_token()
        resp = await client.post(
            '/api/v1/inject',
            json={
                "chat_id": user_id,
                "text": "Continue from here",
                "reply_to_message_id": reply_to_id,
                "wait_for_response": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert 'Subdialog reply' in data['response_text']

        # Verify LLM got the context from the original message
        assert len(mock_llm2.calls) == 1
        messages = mock_llm2.calls[0]['messages']
        all_content = ' '.join(str(m.get('content', '')) for m in messages)
        assert 'Original response' in all_content

    async def test_telegram_then_api_shared_context(self, api_client):
        """Messages sent via Telegram and API share the same conversation context."""
        client, telegram_bot, dp, mock_bot = api_client
        user_id = 55517

        # Message via Telegram
        mock_llm = MockLLMClient()
        mock_llm.add_response("Telegram response")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Telegram message', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.1)

        # Message via API — should see Telegram context
        mock_llm2 = MockLLMClient()
        mock_llm2.add_response("API response")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm2

        token = _wildcard_token()
        resp = await client.post(
            '/api/v1/inject',
            json={"chat_id": user_id, "text": "API message", "wait_for_response": True},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 200

        assert len(mock_llm2.calls) == 1
        messages = mock_llm2.calls[0]['messages']
        all_content = ' '.join(str(m.get('content', '')) for m in messages)
        assert 'Telegram message' in all_content
        assert 'Telegram response' in all_content


class TestGenerateToken:

    def test_generate_wildcard_token(self):
        old_secret = settings.HTTP_API_SECRET
        settings.HTTP_API_SECRET = API_SECRET
        try:
            token = generate_api_token()
            payload = jwt.decode(token, API_SECRET, algorithms=["HS256"])
            assert payload['user_id'] is None
        finally:
            settings.HTTP_API_SECRET = old_secret

    def test_generate_user_token(self):
        old_secret = settings.HTTP_API_SECRET
        settings.HTTP_API_SECRET = API_SECRET
        try:
            token = generate_api_token(user_id=12345)
            payload = jwt.decode(token, API_SECRET, algorithms=["HS256"])
            assert payload['user_id'] == 12345
        finally:
            settings.HTTP_API_SECRET = old_secret
