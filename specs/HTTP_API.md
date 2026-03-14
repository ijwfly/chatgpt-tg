# HTTP API for External Message Injection

> Inject messages into the bot from external systems via HTTP, with full LLM processing and Telegram delivery.

---

## 1. Overview

The HTTP API allows external services to send messages into the bot as if a user had typed them in Telegram. The bot processes the injected message through the full LLM pipeline (context loading, function calling, streaming) and sends the response to the user's Telegram chat.

**Use cases:**
- **External workers** — react to events in external systems (monitoring alerts, CI/CD notifications, webhooks), send context to the bot, and have it respond intelligently in Telegram
- **Async tool results** — the bot starts a long-running task via function calling; the result arrives later through the API

The API reuses the existing transport-agnostic LLM Runtime layer (`app/runtime/`). No changes were needed in `ContextManager`, `DialogManager`, or `DefaultLLMRuntime`.

---

## 2. Architecture

```
External System
      │
      ▼  HTTP POST /api/v1/inject
┌─────────────────────────────────────────────┐
│  aiohttp server (same event loop as bot)    │
│  ├── JWT auth middleware                     │
│  └── inject_handler                         │
│       ├── echo injected message to chat 📨  │
│       ├── build ConversationSession          │
│       ├── build UserInput                    │
│       └── run_injection()                    │
│            ├── build_context_manager()       │
│            ├── DefaultLLMRuntime             │
│            ├── HeadlessRuntimeAdapter        │
│            │    └── consumes RuntimeEvents   │
│            │         sends results to TG     │
│            └── returns response_text + IDs   │
└─────────────────────────────────────────────┘
      │
      ▼  Bot.send_message()
   Telegram
```

### Package Structure

```
app/api/
├── __init__.py
├── headless_adapter.py    # HeadlessSideEffectHandler + HeadlessRuntimeAdapter
└── http_api.py            # aiohttp app, JWT auth, inject handler, run_injection
```

### Key Components

**`HeadlessSideEffectHandler`** — implements `SideEffectHandler` protocol via `Bot.send_message()` / `Bot.send_photo()` directly (no `aiogram.types.Message` needed).

**`HeadlessRuntimeAdapter`** — consumes `RuntimeEvent`s from the runtime and sends results to Telegram as new messages (no streaming/editing). Simplified analog of `TelegramRuntimeAdapter`. Reuses `TelegramRuntimeAdapter._split_dialog_message()` for splitting long messages.

**Echo message** — before LLM processing, the injected text is sent to the chat with a `📨` prefix so the user can see what triggered the response. The `tg_message_id` of the echo message is used for context chain tracking.

---

## 3. Configuration

In `settings.py` / `settings_local.py`:

```python
HTTP_API_ENABLED = True        # Enable the HTTP API server
HTTP_API_PORT = 8080           # Port to listen on
HTTP_API_SECRET = 'your-secret'  # HMAC-SHA256 secret for JWT signing
```

The API server starts automatically with the bot when `HTTP_API_ENABLED = True`. It runs in the same event loop as aiogram polling — no separate process needed.

---

## 4. Authentication

JWT tokens (HMAC-SHA256) are passed in the `Authorization: Bearer <token>` header.

### Token Types

| Token | JWT Payload | Scope |
|-------|-------------|-------|
| Wildcard | `{"user_id": null}` | Can inject to any `chat_id` |
| User-scoped | `{"user_id": 123456}` | Can only inject to `chat_id == 123456` |

### Generating Tokens

```bash
# Wildcard token
python -c "import jwt; print(jwt.encode({'user_id': None}, 'your-secret', algorithm='HS256'))"

# User-scoped token
python -c "import jwt; print(jwt.encode({'user_id': 123456}, 'your-secret', algorithm='HS256'))"
```

Or programmatically:

```python
from app.api.http_api import generate_api_token
token = generate_api_token()             # wildcard
token = generate_api_token(user_id=123)  # user-scoped
```

### Auth Middleware Logic

1. `/api/v1/health` — no auth required
2. Decode JWT with `settings.HTTP_API_SECRET` (HS256)
3. If `user_id` in payload is not null — verify `chat_id == user_id`, else 403
4. If `user_id` is null — allow any `chat_id`

---

## 5. API Endpoints

### `GET /api/v1/health`

Health check. No authentication required.

**Response:** `200 {"status": "ok"}`

### `POST /api/v1/inject`

Inject a message into the bot.

**Request body:**

```json
{
  "chat_id": 123456789,
  "text": "Something happened",
  "images": [{"url": "https://example.com/image.jpg"}],
  "reply_to_message_id": null,
  "wait_for_response": false
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `chat_id` | int | yes | Telegram chat ID (= `telegram_id` for private chats) |
| `text` | string | no* | Message text |
| `images` | list | no* | Images as URLs: `[{"url": "..."}]` |
| `reply_to_message_id` | int | no | Linked mode: creates subdialog branching from this message |
| `wait_for_response` | bool | no | `false` (default): fire-and-forget. `true`: wait for LLM response |

\* At least `text` or `images` must be provided.

**Responses:**

| Status | When | Body |
|--------|------|------|
| 202 | `wait_for_response=false` | `{"status": "accepted"}` |
| 200 | `wait_for_response=true` | `{"status": "ok", "response_text": "...", "tg_message_ids": [...]}` |
| 400 | Missing fields / invalid JSON | `{"error": "..."}` |
| 401 | Missing or invalid JWT | `{"error": "..."}` |
| 403 | User-scoped token, wrong `chat_id` | `{"error": "..."}` |
| 404 | `chat_id` not found in DB | `{"error": "User not found"}` |

---

## 6. Context Modes

### Unlinked (default)

When `reply_to_message_id` is omitted, `DialogManager` finds the last message in the chat (respecting `MESSAGE_EXPIRATION_WINDOW`) or starts a new dialog. This is the same behavior as sending a new message in Telegram.

### Linked (subdialog)

When `reply_to_message_id` is provided, `DialogManager` loads the message branch from that specific message — exactly like replying to a message in Telegram. The LLM sees only the context of that branch.

### Cross-transport context

Messages sent via Telegram and via the API share the same database and context. A message injected via API is visible in the context of subsequent Telegram messages, and vice versa.

---

## 7. Echo Message

When a message is injected, the bot first sends the injected content to the Telegram chat with a `📨` prefix:

```
📨 Something happened
```

For images:

```
📨 Describe this
🖼 https://example.com/image.jpg
```

This serves two purposes:
1. The user sees what triggered the bot's response
2. The `tg_message_id` of the echo message is used for context chain tracking, enabling correct subdialog branching

---

## 8. Pipeline

```python
async def run_injection(bot, db, user, session, user_input, text, images):
    # 1. Echo the injected message to chat
    echo_msg_id = await _echo_injected_message(bot, session.chat_id, text, images)

    # 2. Attach tg_message_id for context tracking
    for ti in user_input.text_inputs:
        if ti.tg_message_id == -1:
            ti.tg_message_id = echo_msg_id

    # 3. Standard runtime pipeline (same as Telegram path)
    context_manager = await build_context_manager(db, user, session)
    side_effects = HeadlessSideEffectHandler(bot, session.chat_id)
    runtime = DefaultLLMRuntime(db, user, side_effects, context_manager)
    adapter = HeadlessRuntimeAdapter(bot, user, session.chat_id, context_manager)
    return await adapter.handle_turn(runtime, user_input, session, lambda: False)
```

---

## 9. Integration with Bot Lifecycle

In `TelegramBot`:

- **`on_startup`**: if `HTTP_API_ENABLED`, creates `aiohttp.web.Application`, sets up `AppRunner` + `TCPSite` on the configured port
- **`on_shutdown`**: cleans up the API runner

The aiohttp `TCPSite` runs in the same asyncio event loop as aiogram polling — no threading or multiprocessing.

---

## 10. Testing

### Automated (E2E)

`tests/e2e/test_http_api.py` — 23 tests covering:

- Health endpoint (no auth)
- Auth: missing header, invalid token, wrong secret, user-scoped token scope checks, wildcard token
- Validation: missing chat_id, missing text/images, invalid JSON, unknown user
- Injection: fire-and-forget (202), wait-for-response (200), LLM receives injected text, context persistence across injections, images (with text and without), linked mode with reply_to, cross-transport context (Telegram + API), echo message display, echo with images
- Token generation utility

### Manual

`test_manual_callback.sh` — shell script for manual testing against a running bot instance. Generates JWT tokens and runs through all endpoint scenarios with curl.
