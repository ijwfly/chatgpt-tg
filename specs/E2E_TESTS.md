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
| `editMessageReplyMarkup` | Same as sendMessage | Settings toggle (update keyboard) |
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
│   ├── test_sub_dialogue.py        # Multi-message dialogue context (1 test)
│   ├── test_function_calling.py    # Tool calling via SaveUserSettings (3 tests)
│   ├── test_streaming.py           # Streaming responses and thinking blocks (2 tests)
│   ├── test_context_management.py  # Reset, expiration, reply branching (3 tests)
│   ├── test_settings.py            # Settings menu and toggles (3 tests)
│   ├── test_forwarded_messages.py  # Forwarded message context (1 test)
│   ├── test_error_handling.py      # Error conditions (1 test)
│   ├── test_agent_runtime.py      # Agent runtime, plans, background tasks (26 tests)
│   └── test_scheduled_tasks.py    # Scheduled task CRUD and execution (9 tests)
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
- `make_forward_message(text, forward_sender_name, forward_from, ...)` — forwarded message update
- `make_callback_query(data, message_id, ...)` — callback query update

Auto-incrementing counters for `update_id` and `message_id`.

### mock_llm_client.py

`MockLLMClient(BaseLLMClient)` — mock that returns canned responses from a queue:

- `add_response(content, tool_calls, function_call, prompt_tokens, completion_tokens)` — enqueue a sync response
- `add_streaming_response(content_chunks, tool_calls, prompt_tokens, completion_tokens)` — enqueue a streaming response (async generator of chunks)
- `chat_completions_create()` — pops first response, builds mock matching OpenAI SDK shape. When `stream=True` and response is streaming, returns async generator
- `calls` list — records all calls with model, messages, additional_fields
- `MockUsage` — supports `dict()` conversion via `__iter__` (needed by `CompletionUsage(**dict(resp.usage))`)
- `MockDelta` — streaming chunk delta with proper `dict()` support for `merge_dicts()`

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

### test_function_calling.py (3 tests)

| Test | Scenario | Verifies |
|------|----------|----------|
| `test_tool_call_executes_and_returns_to_llm` | LLM returns tool_call for `save_user_settings`, then final response | Tool execution, DB update, response passed back to LLM |
| `test_function_call_verbose_shows_details` | Same with `function_call_verbose=True` | Verbose output with function name in message |
| `test_successive_function_call_limit` | LLM returns tool_calls exceeding limit | Error message sent, exception raised |

**Pipeline covered:** `MessageProcessor -> FunctionManager -> FunctionStorage -> SaveUserSettings -> recursive handle_gpt_response`

### test_streaming.py (2 tests)

| Test | Scenario | Verifies |
|------|----------|----------|
| `test_streaming_sends_and_edits_message` | Streaming response with multiple chunks | sendMessage during streaming + editMessageText after |
| `test_streaming_with_thinking_blocks` | Streaming with `<think>` tags | Thinking emoji shown during thinking, final content visible |

**Pipeline covered:** `ChatGptManager.send_user_message_streaming -> ChatGPT.send_messages_streaming -> handle_response_generator (streaming edits, thinking parsing)`

### test_context_management.py (3 tests)

| Test | Scenario | Verifies |
|------|----------|----------|
| `test_reset_clears_context` | Send A -> /reset -> send B | LLM context for B doesn't contain A |
| `test_message_expiration_starts_fresh_context` | Send A -> age messages 2h -> send B | Expired messages excluded from context |
| `test_reply_to_bot_message_loads_branch` | Send A -> /reset -> send B -> reply to A's response | Reply loads branch A context, not branch B |

**Pipeline covered:** `DialogManager.process_dialog (reset, expiration, reply branching) -> DB message chain`

### test_settings.py (3 tests)

| Test | Scenario | Verifies |
|------|----------|----------|
| `test_settings_command_shows_menu` | Send `/settings` | Bot sends message with "Settings:" text |
| `test_toggle_setting_updates_db` | Callback query `settings.streaming_answers` | Setting value flipped in DB |
| `test_hide_settings_deletes_message` | Callback query `settings.hide` | Bot calls deleteMessage |

**Pipeline covered:** `Settings.send_settings -> process_callback -> toggle_setting -> DB update / delete_message`

### test_forwarded_messages.py (1 test)

| Test | Scenario | Verifies |
|------|----------|----------|
| `test_forwarded_message_as_context` | Forward message + prompt | LLM context includes sender name and forwarded content |

**Pipeline covered:** `BatchedInputHandler.handle_forwarded_message -> MessageProcessor.add_text_as_context`

### test_error_handling.py (1 test)

| Test | Scenario | Verifies |
|------|----------|----------|
| `test_error_on_no_llm_response` | Empty MockLLMClient response queue | "Something went wrong" message sent |

**Pipeline covered:** `process_batch exception handler -> message.answer with error`

### test_agent_runtime.py (26 tests)

Covers: AgentRuntime (simple response, multi-turn tool loop, iteration limit), PlanManager (create, get, update step, auto-complete, delete, no active plan), BackgroundTaskManager (spawn, check, cancel, timeout), AgentTools (SpawnTask, CheckTask, CreatePlan, UpdatePlanStep, GetPlan, DeletePlan via mock LLM tool calls).

**Pipeline covered:** `MessageProcessor -> AgentRuntime._agent_loop -> PlanManager -> BackgroundTaskManager -> agent tools -> DB`

### test_scheduled_tasks.py (9 tests)

Covers: ScheduleTask creation (one-time via dateparser, recurring via cron), ListScheduledTasks, CancelScheduledTask, SchedulerService (poll, execute, disable after one-time execution, recurring next_execution update, error handling).

**Pipeline covered:** `ScheduleTask tool -> DB -> SchedulerService.poll -> execute_task -> AgentRuntime`

---

## Not Yet Covered

| Scenario | Why | Priority |
|----------|-----|----------|
| Image generation (DALL-E) | Needs mock of `OpenAIAsync.instance().images.generate()` + `httpx` | Low |
| Voice input (Whisper) | Needs mock of file download + pydub + Whisper API | Low |
| Context auto-summarization | Needs enough messages to exceed `short_term_memory_tokens` | Low |
| Access control (role gating) | Needs user with insufficient role | Low |
| Cancellation (streaming stop) | Needs streaming mock + callback query | Low |
| Anthropic models | Needs `AnthropicChatGPT` path + different mock shape | Low |
| MCP tool calling | Agent tools are tested, but MCP protocol-level calls need MCP server mock | Low |

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
| `ENABLE_AGENT_RUNTIME` | `True` | Agent mode enabled in tests |
| `MCP_SERVERS_AGENT` | `[]` | No agent MCP connections |

---

## Critical Implementation Details

1. **`get_models()` uses `@lru_cache`** — must call `get_models.cache_clear()` after patching settings
2. **`LLMClientFactory._model_clients` is class-level** — cleared per test to avoid cross-test contamination
3. **`DBFactory.connection_pool` is class-level** — pre-set in bot_app fixture to reuse session pool
4. **`monthly_usage_task.start()` in on_startup** — creates background task, stopped in teardown
5. **Streaming mock** — `add_streaming_response()` returns an async generator with `MockDelta` objects supporting `dict()` conversion (required by `merge_dicts()` in `send_messages_streaming`)
6. **`TypingWorker`** — background task calls `bot.send_chat_action` in a loop; handled by Bot.request mock returning `True`
7. **Error tests** — `process_batch` sends "Something went wrong" then re-raises; tests must use `pytest.raises(ValueError)` around `dp.process_update()`
8. **Streaming edits timing** — `WAIT_BETWEEN_MESSAGE_UPDATES = 2s` prevents edits in fast tests; streaming tests verify sendMessage during streaming + editMessageText after (from `handle_gpt_response`)
