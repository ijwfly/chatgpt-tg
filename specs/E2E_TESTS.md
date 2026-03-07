# E2E Testing Infrastructure

## Overview

E2E tests exercise the full message pipeline — from "user sends message" to "bot replies" — using real PostgreSQL but mocked LLM APIs and Telegram transport. Zero production code changes required.

## Architecture

### What's real vs mocked

| Component | Strategy | Why |
|-----------|----------|-----|
| PostgreSQL | **Real** (docker container) | Critical path — migrations, queries, data integrity |
| LLM APIs | **Mocked** (`MockLLMClient` injected via `LLMClientFactory._model_clients`) | Deterministic, no API costs |
| Telegram transport | **Mocked** (`Bot.request` AsyncMock) | No real bot token needed |
| Batching Timer | **Patched** to 0.001s | Fast tests without 0.3s waits |
| Functions (WolframAlpha, Todoist, etc.) | **Disabled** via settings | Not needed for core flow tests |
| MCP Servers | **Disabled** (`MCP_SERVERS = []`) | Not needed for core flow tests |

### Key approach: `dp.process_update()`

aiogram 2.x `Dispatcher.process_update(update)` is a public async method that runs the full handler pipeline: middleware -> message handlers -> response. We construct fake `types.Update` objects and feed them into the dispatcher.

### Bot.request mock

All outgoing Telegram calls funnel through `Bot.request(method_name, data)`. The mock returns valid dicts for each method:

| method | Return | Used by |
|--------|--------|---------|
| `sendMessage` | `{message_id, from, chat, date, text}` | Response messages, function verbose output |
| `editMessageText` | Same as sendMessage | Streaming updates |
| `sendPhoto` | Same as sendMessage | DALL-E image results |
| `sendChatAction` | `True` | TypingWorker background loop |
| `deleteMessage` | `True` | /usage, /settings, /models |
| `answerCallbackQuery` | `True` | Inline button callbacks |
| `setMyCommands` | `True` | on_startup bot commands |

### TelegramObject.bot monkey-patch

aiogram 2.x uses `ContextVar` for `Bot` instance, which breaks across asyncio tasks (Timer batching, TypingWorker). The test infrastructure patches `TelegramObject.bot` property to always return the mock bot, avoiding ContextVar issues.

---

## File Structure

```
tests/
├── conftest.py                     # Core fixtures: db_pool, db, mock_bot, bot_app, clean_db
├── helpers/
│   ├── __init__.py
│   ├── telegram_factory.py         # Factory for aiogram Update/Message objects
│   ├── mock_llm_client.py          # MockLLMClient with canned response queue
│   └── bot_spy.py                  # Assertion helpers over captured Bot.request calls
├── e2e/
│   ├── __init__.py
│   ├── test_simple_message.py      # Text message -> LLM response (4 tests)
│   ├── test_commands.py            # /reset, /usage (2 tests)
│   └── test_sub_dialogue.py        # Multi-message dialogue context (1 test)
```

---

## Test Fixtures (conftest.py)

| Fixture | Scope | Purpose |
|---------|-------|---------|
| `event_loop` | session | Single asyncio event loop for all tests |
| `db_pool` | session | asyncpg connection pool to test postgres |
| `db` | session | `DB` instance wrapping the pool |
| `clean_db` | function, autouse | DELETEs all rows from all tables after each test |
| `mock_bot` | function | `Bot` with `request` replaced by AsyncMock |
| `spy` | function | `BotSpy` wrapping mock_bot for assertions |
| `bot_app` | function | Full `TelegramBot` + `Dispatcher`, initialized via `on_startup`. Patches Timer, clears LLM client cache, injects test DB pool |

### bot_app lifecycle

1. Creates `Dispatcher` and `TelegramBot`
2. Patches `Timer.__init__` to 0.001s timeout
3. Clears `LLMClientFactory._model_clients`
4. Sets `DBFactory.connection_pool` to test pool (prevents on_startup from creating its own)
5. Patches `TelegramObject.bot` property
6. Calls `on_startup(None)` — registers handlers, middleware, starts scheduled tasks
7. Yields `(telegram_bot, dp, mock_bot)`
8. Teardown: restores `TelegramObject.bot`, stops `monthly_usage_task`, restores LLM clients

---

## Helper Components

### telegram_factory.py

Factory functions for creating fake aiogram `types.Update` objects:

- `make_text_message(text, user_id, chat_id, reply_to_message_id, ...)` — text message update
- `make_command_message(command, ...)` — `/command` message update
- `make_callback_query(data, message_id, ...)` — callback query update

Auto-incrementing counters for `update_id` and `message_id`.

### mock_llm_client.py

`MockLLMClient(BaseLLMClient)` — mock that returns canned responses from a queue:

- `add_response(content, tool_calls, function_call, prompt_tokens, completion_tokens)` — enqueue a response
- `chat_completions_create()` — pops first response, builds mock matching OpenAI SDK shape
- `calls` list — records all calls with model, messages, additional_fields
- `MockUsage` — supports `dict()` conversion via `__iter__` (needed by `CompletionUsage(**dict(resp.usage))`)

Currently only supports sync mode (`stream=False`). Streaming mock can be added later.

### bot_spy.py

`BotSpy(mock_bot)` — assertion helpers over captured `Bot.request` calls:

- `get_sent_messages()` — all `sendMessage` calls
- `get_edited_messages()` — all `editMessageText` calls
- `get_last_sent_text()` — text of last sent message
- `get_all_sent_texts()` / `get_all_edited_texts()` — all texts
- `assert_sent_text_contains(substring)` — asserts substring in any sent/edited message
- `assert_any_message_sent()` — asserts at least one sendMessage

---

## Covered Scenarios

### test_simple_message.py (4 tests)

| Test | Scenario | Verifies |
|------|----------|----------|
| `test_text_message_gets_response` | Send text -> LLM responds | Full pipeline: middleware -> batching -> context -> LLM -> telegram response. Bot sends message containing LLM output |
| `test_user_created_in_db` | Send text from new user | UserMiddleware creates user in DB with correct telegram_id |
| `test_message_saved_in_db` | Send text -> response | Both user message and bot response are persisted in chatgpttg.message |
| `test_llm_receives_user_message` | Send text | LLM client receives the user's text in context messages (system prompt + user message) |

**Pipeline covered:** `UserMiddleware -> BatchedInputHandler -> MessageProcessor -> ContextManager -> DialogManager -> ChatGPT -> ChatGptManager -> send_telegram_message -> DB persistence`

### test_commands.py (2 tests)

| Test | Scenario | Verifies |
|------|----------|----------|
| `test_reset_command` | Send `/reset` | Bot creates reset message in DB, responds with acknowledgment emoji |
| `test_usage_command` | Send `/usage` | Bot deletes command message, responds with usage text containing "Total:" |

**Pipeline covered:** `UserMiddleware -> command handler -> DB query -> telegram response`

### test_sub_dialogue.py (1 test)

| Test | Scenario | Verifies |
|------|----------|----------|
| `test_reply_chain_context` | Send message A, get response, send message B | Second LLM call receives context from both messages (linear dialogue continuity) |

**Pipeline covered:** `Multi-turn conversation: message A -> response A -> message B -> LLM gets context [A, response_A, B]`

---

## Not Yet Covered

| Scenario | Why | Priority |
|----------|-----|----------|
| Streaming responses | Requires async generator mock for `send_messages_streaming()` | Medium |
| Sub-dialogue via reply | Needs `reply_to_message_id` pointing to a real DB-stored tg_message_id | Medium |
| Function/tool calling | Needs MockLLMClient to return `tool_calls`, then a second response | Medium |
| Image generation (DALL-E) | Needs mock of `OpenAIAsync.instance().images.generate()` + `httpx` | Low |
| Voice input (Whisper) | Needs mock of file download + pydub + Whisper API | Low |
| Context auto-summarization | Needs enough messages to exceed `short_term_memory_tokens` | Low |
| Message expiration | Needs time manipulation (messages older than 1 hour) | Low |
| Access control (role gating) | Needs user with insufficient role | Low |
| Settings/models menus | Needs callback query handling + inline keyboard parsing | Low |
| Cancellation (streaming stop) | Needs streaming mock + callback query | Low |
| Anthropic models | Needs `AnthropicChatGPT` path + different mock shape | Low |
| MCP tool calling | Needs MCP server mock | Low |

---

## Settings Overrides for Tests

Applied in `conftest.py` before any app imports:

| Setting | Test Value | Why |
|---------|-----------|-----|
| `OPENAI_TOKEN` | `'test-openai-key'` | Models register in `get_models()` |
| `TELEGRAM_BOT_TOKEN` | `'123456:TEST-TOKEN'` | Bot instantiation |
| `ANTHROPIC_TOKEN` | `''` | Disable Anthropic models |
| `OPENROUTER_TOKEN` | `''` | Disable OpenRouter models |
| `USER_ROLE_DEFAULT` | `UserRole.ADMIN` | Test users get full access |
| `USER_ROLE_BOT_ACCESS` | `UserRole.STRANGER` | No access gating |
| `ENABLE_WOLFRAMALPHA` | `False` | No external API calls |
| `VECTARA_RAG_ENABLED` | `False` | No external API calls |
| `ENABLE_TODOIST_ADMIN_INTEGRATION` | `False` | No external API calls |
| `ENABLE_OBSIDIAN_ECHO_ADMIN_INTEGRATION` | `False` | No external API calls |
| `ENABLE_USER_ROLE_MANAGER_CHAT` | `False` | No admin notifications |
| `MCP_SERVERS` | `[]` | No MCP connections |

---

## Critical Implementation Details

1. **`get_models()` uses `@lru_cache`** — must call `get_models.cache_clear()` after patching settings
2. **`LLMClientFactory._model_clients` is class-level** — cleared per test to avoid cross-test contamination
3. **`DBFactory.connection_pool` is class-level** — pre-set in bot_app fixture to reuse session pool
4. **`monthly_usage_task.start()` in on_startup** — creates background task, stopped in teardown
5. **Sync mode only** — initial tests use `user.streaming_answers = False` (default for new users). Streaming tests need async generator mocks
6. **`TypingWorker`** — background task calls `bot.send_chat_action` in a loop; handled by Bot.request mock returning `True`
