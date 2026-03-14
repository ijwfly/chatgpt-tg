import asyncio
import logging
from typing import Optional

import jwt
from aiohttp import web

import settings
from app.api.headless_adapter import HeadlessSideEffectHandler, HeadlessRuntimeAdapter
from app.context.context_manager import build_context_manager
from app.context.dialog_manager import DialogUtils
from app.runtime.conversation_session import ConversationSession
from app.runtime.default_runtime import DefaultLLMRuntime
from app.runtime.user_input import UserInput, TextInput
from app.storage.db import DB

logger = logging.getLogger(__name__)


def generate_api_token(user_id: int = None) -> str:
    """Generate a JWT token. If user_id is None, creates a wildcard token."""
    return jwt.encode({"user_id": user_id}, settings.HTTP_API_SECRET, algorithm="HS256")


@web.middleware
async def auth_middleware(request, handler):
    if request.path == '/api/v1/health':
        return await handler(request)

    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return web.json_response({"error": "Missing or invalid Authorization header"}, status=401)

    token = auth_header[len('Bearer '):]
    try:
        payload = jwt.decode(token, settings.HTTP_API_SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return web.json_response({"error": "Invalid token"}, status=401)

    request['jwt_payload'] = payload
    return await handler(request)


async def health_handler(request):
    return web.json_response({"status": "ok"})


async def inject_handler(request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    chat_id = body.get('chat_id')
    if chat_id is None:
        return web.json_response({"error": "chat_id is required"}, status=400)

    text = body.get('text')
    images = body.get('images')
    if not text and not images:
        return web.json_response({"error": "At least text or images must be provided"}, status=400)

    # JWT scope check
    jwt_payload = request['jwt_payload']
    jwt_user_id = jwt_payload.get('user_id')
    if jwt_user_id is not None and jwt_user_id != chat_id:
        return web.json_response({"error": "Token not authorized for this chat_id"}, status=403)

    reply_to_message_id = body.get('reply_to_message_id')
    wait_for_response = body.get('wait_for_response', False)

    bot = request.app['bot']
    db: DB = request.app['db']

    user = await db.get_user(chat_id)
    if user is None:
        return web.json_response({"error": "User not found"}, status=404)

    session = ConversationSession(
        chat_id=chat_id,
        reply_to_message_id=reply_to_message_id,
    )

    user_input = _build_user_input(text, images)

    if wait_for_response:
        result = await run_injection(bot, db, user, session, user_input, text, images)
        return web.json_response({
            "status": "ok",
            "response_text": result["response_text"],
            "tg_message_ids": result["tg_message_ids"],
        })
    else:
        asyncio.create_task(run_injection(bot, db, user, session, user_input, text, images))
        return web.json_response({"status": "accepted"}, status=202)


def _build_user_input(text: Optional[str], images: Optional[list]) -> UserInput:
    user_input = UserInput()

    if images:
        content = []
        if text:
            content.append(DialogUtils.construct_message_content_part(DialogUtils.CONTENT_TEXT, text))
        for img in images:
            url = img.get('url', '')
            content.append(DialogUtils.construct_message_content_part(DialogUtils.CONTENT_IMAGE_URL, url))
        user_input.text_inputs.append(TextInput(text=content if content else None))
    elif text:
        user_input.text_inputs.append(TextInput(text=text))

    return user_input


async def _echo_injected_message(bot, chat_id, text, images):
    """Send the injected message to the chat so the user can see what triggered the response.

    Returns the tg_message_id of the echo message.
    """
    parts = []
    if text:
        parts.append(text)
    if images:
        urls = [img.get('url', '') for img in images]
        parts.append('\n'.join(f'🖼 {url}' for url in urls))

    echo_text = f'📨 {chr(10).join(parts)}'
    msg = await bot.send_message(chat_id, echo_text)
    return msg.message_id


async def run_injection(bot, db, user, session, user_input, text=None, images=None):
    try:
        echo_msg_id = await _echo_injected_message(bot, session.chat_id, text, images)

        # Attach the real tg_message_id to user input so context tracks it correctly
        for ti in user_input.text_inputs:
            if ti.tg_message_id == -1:
                ti.tg_message_id = echo_msg_id

        context_manager = await build_context_manager(db, user, session)
        side_effects = HeadlessSideEffectHandler(bot, session.chat_id)
        runtime = DefaultLLMRuntime(db, user, side_effects, context_manager)
        adapter = HeadlessRuntimeAdapter(bot, user, session.chat_id, context_manager)
        return await adapter.handle_turn(runtime, user_input, session, lambda: False)
    except Exception:
        logger.exception("Error in run_injection for chat_id=%s", session.chat_id)
        return {"response_text": "", "tg_message_ids": []}


def create_api_app(bot, db) -> web.Application:
    app = web.Application(middlewares=[auth_middleware])
    app['bot'] = bot
    app['db'] = db
    app.router.add_get('/api/v1/health', health_handler)
    app.router.add_post('/api/v1/inject', inject_handler)
    return app
