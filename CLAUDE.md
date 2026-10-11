# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Telegram bot that provides access to multiple LLM providers (OpenAI, Anthropic, OpenRouter, local LM Studio) with features like streaming responses, function/tool calling, voice transcription (Whisper), TTS, automatic context summarization, and MCP server integration.

## Running the Project

```bash
docker-compose up -d          # Start all services (app, postgres, image_proxy, sandbox, pgweb)
docker-compose up -d --build  # Rebuild and start
docker-compose logs -f app    # View app logs
```

The app entrypoint is `main.py`. Note: `docker-compose.yml` has the app entrypoint overridden to a sleep loop for development — switch to `python main.py` for actual execution.

Database migrations run automatically on Postgres startup via `migrations/pg_init.sh` which executes SQL files from `migrations/sql/` in order.

## Configuration

All configuration is in `settings.py`. The file has defaults at the top and local overrides at the bottom. API keys, tokens, and role settings are all configured there. **`settings.py` currently contains hardcoded secrets — these should not be committed.**

## Architecture

### Request Flow
1. `main.py` → creates aiogram `Bot`/`Dispatcher`, initializes `TelegramBot`
2. `TelegramBot` (`app/bot/telegram_bot.py`) — registers handlers, sets up middleware, manages lifecycle
3. `BatchedInputHandler` (`app/bot/batched_input_handler.py`) — collects user messages into batches, does transport preprocessing (Whisper), builds transport-agnostic `UserInput`
4. `MessageProcessor` (`app/bot/message_processor.py`) — thin adapter: builds `ConversationSession`, creates `DefaultLLMRuntime` + `TelegramRuntimeAdapter`, delegates execution
5. `DefaultLLMRuntime` (`app/runtime/default_runtime.py`) — adds user input to context, calls LLM, handles tool call loop, yields `RuntimeEvent`s
6. `TelegramRuntimeAdapter` (`app/bot/telegram_runtime_adapter.py`) — consumes RuntimeEvents: streaming message editing, thinking emoji, cancel button, verbose function output, saves assistant responses to context
7. `ContextManager` (`app/context/context_manager.py`) — facade over `DialogManager` and `FunctionManager`
8. `DialogManager` (`app/context/dialog_manager.py`) — loads conversation history from DB, handles sub-dialogues (reply chains), auto-summarizes when context exceeds token limits
9. `ChatGPT` / `AnthropicChatGPT` (`app/openai_helpers/chatgpt.py`, `app/openai_helpers/anthropic_chatgpt.py`) — LLM API interaction, streaming, response parsing

### LLM Runtime Layer
- `app/runtime/runtime.py` — `LLMRuntime` protocol: `process_turn(user_input, session, is_cancelled) -> AsyncGenerator[RuntimeEvent]`
- `app/runtime/default_runtime.py` — `DefaultLLMRuntime`: current implementation using ChatGPT/Anthropic clients
- `app/runtime/events.py` — event hierarchy: `StreamingContentDelta`, `FinalResponse`, `FunctionCallStarted`, `FunctionCallCompleted`, `ErrorEvent`
- `app/runtime/user_input.py` — `UserInput` with `TextInput`, `ImageInput`, `VoiceTranscription`
- `app/runtime/side_effects.py` — `SideEffectHandler` protocol for transport-agnostic function side effects
- `app/runtime/context_utils.py` — shared `add_user_input_to_context()` used by runtime and context-only path
- See `specs/RUNTIME_ARCHITECTURE.md` for full details on how to add new runtimes and transports

### Multi-Provider LLM Support
- `app/llm_models.py` — defines all models via `LLModel` class with pricing, context config, capabilities, and API client type
- `app/openai_helpers/llm_client.py` — client hierarchy: `BaseLLMClient` → `GenericAsyncOpenAIClient` (OpenAI-compatible APIs) → `OpenAISpecificAsyncOpenAIClient` (OpenAI-specific features like stream usage); `AnthropicAsyncClient` (Anthropic native API)
- `app/openai_helpers/llm_client_factory.py` — creates/caches client instances per model
- To add a new model: add entry in `get_models()` in `llm_models.py` with appropriate client class, capabilities, and context configuration

### Function/Tool Calling
- `app/functions/base.py` — `OpenAIFunction` base class. Accepts `SideEffectHandler` (not aiogram Message) for transport interactions. Subclasses define params via Pydantic `PARAMS_SCHEMA`, implement `run()`, provide `get_description()` and optional `get_system_prompt_addition()`
- `app/openai_helpers/function_storage.py` — `FunctionStorage` registry, converts functions to OpenAI function/tool format
- `app/context/function_manager.py` — decides which functions to register based on settings and user role
- Built-in functions: `wolframalpha`, `save_user_settings`
- Web agents (`app/functions/web_agents.py`, enabled via `ENABLE_WEB_AGENTS` + `TAVILY_API_KEY`): `web_search_agent` and `web_scraper_agent` — each runs an isolated LLM sub-agent (`app/runtime/web_agent_runner.py`, clean context, billed usage) equipped with internal Tavily tools (`tavily_search`/`tavily_extract`, client in `app/web/tavily_client.py`); registered in both `FunctionManager` and `AgentRuntime`
- MCP integration: `app/functions/mcp/` — dynamically loads tools from configured MCP servers

### Bash Sandbox (agent mode)
- `sandbox/` — separate docker-compose service (Ubuntu-based, no published ports). `/workspace/user_<telegram_id>` is the user's actual Linux home, `$HOME`, and cwd, persisted by `sandbox_workspace:/workspace` with mode 0700. `sandbox/server/identities.py` stores stable UID/GID in root-only `.sandbox/users.json`, restores accounts before serving requests, and refuses unregistered legacy workspaces. No recursive runtime chown. HTTP API: `POST /exec`, `POST /fileop`, `GET/PUT /files/{path}`, `GET /skills`; all except `/health` require `Authorization: Bearer <SANDBOX_API_TOKEN>` and `X-User-Id`. The token must match in app/sandbox and is excluded from user subprocess environments. User commands run through `sudo -H` with private umask and no startup files; file helpers use isolated Python. Paths stay confined by `resolve_path`, with read-only access to shared skills.
- `app/sandbox/client.py` — authenticated `SandboxClient` (httpx), raises `SandboxError`; exclusive uploads return HTTP 409 on conflicts (`SandboxFileExists`). `upload_workspace_file` chooses suffixes atomically through exclusive uploads, including concurrent collisions.
- `scripts/migrate_sandbox_homes.py` — stdlib operator CLI `plan/export/apply/verify`: backups of the full volume and legacy `/home` plus account identities/checksums, offline conflict-aware merge, private registry/layout and repeatable verification. Mutations require stopped legacy container and no running volume users. Existing root work files are not relocated. See README before rebuilding a legacy sandbox.
- Home layout: `inbox/` originals, `projects/<task>/` work, `outbox/<task>/` deliverables, `tmp/<task>/` scratch/staging, `skills/<name>/` installed skills; application config/data stays in standard dot-directories. These instructions reach both main agent and sub-agents.
- `app/functions/bash_sandbox.py` — agent tools: `bash_exec`, `read_file`, `write_file`, `edit_file`, `send_file_to_chat`, `save_image_to_workspace`. Registered in `AgentRuntime` when `settings.ENABLE_BASH_SANDBOX` (off by default) — so available only with `agent_mode=on`
- Incoming Telegram documents: with `agent_mode=on` and sandbox enabled, `BatchedInputHandler.handle_document_sandbox` saves files in `inbox/` and includes the actual relative path in the `[file uploaded to agent workspace]` context message and confirmation. Both Telegram messages resolve to that context row via `message_tg_alias`. Captions trigger an answer via `UserInput.force_prompt`; forwarded captions follow `forward_as_prompt`.
- Incoming images stay lazy and labeled `[image #N]` in context. `save_image_to_workspace` downloads through side effects only on demand; its default destination is `inbox/photo_<N>.jpg`, while explicit paths are honored. Naming/upload collision handling is shared with documents.
- Outgoing files: `send_file_to_chat` downloads from the sandbox and sends via the `send_document` side effect (`SideEffectHandler` protocol + `TelegramSideEffectHandler`); it sets `result_tg_message_id`, so the tool response row carries the document's tg id and a reply to the file continues the dialog branch

### Skills (agent mode)
- A skill is a folder with `SKILL.md` (YAML frontmatter `name` + `description`, then markdown instructions) and optional `reference/`, `scripts/`, `templates/`. Format is compatible with Claude Code Agent Skills
- Two locations: personal `skills/` inside the user's home and shared read-only `/workspace/public_skills`. Built-in skills from `sandbox/skills/` are copied into the image and re-synced by `common.initialize_sandbox()` before serving on every startup (bundled ones are overwritten, admin-added ones are left alone). `sandbox/skills/skill-creator` teaches skill creation and ships `scripts/validate_skill.py`.
- Only the **catalog** reaches the LLM: `AgentRuntime` calls `get_skills_prompt_addition()` (`app/skills/catalog.py`) once per turn, which hits `GET /skills` and renders a `## Skills` block (name, description, path) appended to the system prompt and to the sub-agent prompt. Bodies are read by the model itself with `read_file` — no dedicated tools. A personal skill shadows a shared one with the same name; the list is capped by `SKILLS_MAX_COUNT` / `SKILLS_MAX_DESCRIPTION_CHARS`; a sandbox failure only logs a warning and drops the block
- Gated by `ENABLE_SKILLS` + `ENABLE_BASH_SANDBOX` + `agent_mode`

### Database
- PostgreSQL via `asyncpg`, no ORM
- `app/storage/db.py` — `DB` class with raw SQL queries, `DBFactory` manages connection pool
- Schema in `chatgpttg` schema, tables: `user`, `message`, `completion_usage`, `whisper_usage`, `tts_usage` (plus `image_generation_usage`, kept only as billing history of the removed DALL-E 3 tool)
- Messages store full dialog history as JSON with `previous_message_ids` for branching sub-dialogues

### Key Patterns
- **Sub-dialogues**: replying to a message creates a branch — `DialogManager` loads only that branch's history. Chat messages without a context row of their own (upload confirmations, the user's voice message behind a transcription) are registered as aliases in `message_tg_alias`, so replying to them resolves to the right row
- **Interrupted tool calls**: the assistant `tool_calls` row is saved before the tools run, each `tool` result after its tool. If the turn dies in between (task cancelled on restart, transport error in the adapter, generator closed) the runtimes' tool loops catch `BaseException` and save an `Error: … interrupted` result for every unanswered call (`close_unanswered_tool_calls` in `app/runtime/context_utils.py`); `DialogManager.get_dialog_messages` additionally repairs already broken histories on load (`app/context/tool_call_repair.py`). Both OpenAI and Anthropic reject a `tool_calls` message without results. The adapter and both runtimes wrap nested event generators in `contextlib.aclosing` so this cleanup runs in the same task instead of at garbage collection
- **Context expiration**: messages older than `MESSAGE_EXPIRATION_WINDOW` (default 1h) start fresh context
- **Auto-summarization**: when context exceeds `short_term_memory_tokens`, older messages get summarized via LLM
- **Rich messages**: LLM answers, `/usage`, `/models` and admin cards are sent as Telegram Rich Messages (`sendRichMessage` with `InputRichMessage(markdown=...)`, GFM markdown, 32768-char limit) via `app/bot/rich_messages.py`; a rejected markup falls back to plain `sendMessage`. Service texts (errors, confirmations, transcriptions, verbose tool output) stay plain via `utils.send_telegram_message`. See `specs/RICH_MESSAGES.md`
- **Tool status hints**: while a tool runs, the chat shows `FunctionCallStarted.status_message` — the tool's `get_status_message()` title plus a short detail of the actual call (`Running bash command: ls -la /workspace`, `Searching the web: <query>`). Tools declare `STATUS_DETAIL_PARAM = '<param>'` or override `get_status_detail(params)`; `build_status_message()` in `app/functions/base.py` parses the raw JSON args, one-lines and caps the detail (`FUNCTION_HINT_DETAIL_MAX_CHARS`), and never raises. `TelegramRuntimeAdapter._format_hint` escapes the hint for rich markdown. Gated by the user's `function_call_hints` setting
- **Streaming**: by default a real rich message is created and edited every second with an inline Stop button (`ChatServiceMessage`). With `settings.RICH_DRAFT_STREAMING = True` (off by default — clients render drafts poorly) private chats stream an ephemeral rich draft (`sendRichMessageDraft`, `DraftStream` in `app/bot/service_message.py`, thinking/tool hints as `<tg-thinking>`, native Stop button via `can_stop`), finished with a fresh `sendRichMessage`; groups (or a failed draft call) fall back to the edit path. The Bot API 10.3 `stopped_message_generation` update is handled by `CancellationManager.process_stopped_generation` (registered on `dp.stopped_message_generation`, aiogram ≥ 3.31; the manager is created in `TelegramBot.__init__` because `start_polling` derives `allowed_updates` before the startup hooks run). Live output is paced by one `SendGate` per turn (`app/bot/service_message.py`): at most one draft/edit per `WAIT_BETWEEN_MESSAGE_UPDATES`, the latest state is flushed later instead of dropped, and `TelegramRetryAfter` only extends the hold-off; final answers retry once via `with_flood_retry`
- **`<think>` tags**: models that output thinking blocks have them parsed, displayed as emoji status during streaming, then stripped before saving
- **User roles**: `UserRole` enum (STRANGER, BASIC, ADVANCED, ADMIN, NOONE) gates access to features and models
- **Image proxy**: `main_image_proxy.py` serves Telegram file IDs as URLs for OpenAI vision API
- **Observability**: Langfuse tracing behind the `app/observability/` facade (no-op when disabled). Rule: `langfuse`/`opentelemetry` may only be imported inside that package; runtimes call `begin_turn`/`tool_span`/`agent_span` (1–2 lines per call site). One trace per turn, `session_id` = dialog tree root, `user_id` = db id. See `specs/OBSERVABILITY.md` for details and the removal recipe

## Testing

**After any code changes, always run the E2E tests and verify they pass:**

```bash
bash scripts/test.sh
```

The script starts a test PostgreSQL container, runs all tests, and tears down the container. All tests must pass before considering the work done. If a test fails, fix the issue before committing.

Test details: `specs/E2E_TESTS.md` (Telegram is mocked with a recording `BaseSession`; updates are fed via `dp.feed_update(mock_bot, update)`; `BotSpy` reads text from both `sendMessage.text` and `sendRichMessage.rich_message.markdown`, drafts via `get_drafts()`)

### Libraries
- `aiogram` 3.x (Telegram bot framework; handlers registered via `dp.message.register(...)`/`dp.callback_query.register(...)` with `Command`/`F` filters, `UserMiddleware` is a `dp.message` middleware, outgoing files are `BufferedInputFile`/`FSInputFile`)
- `openai` (OpenAI API)
- `anthropic` (Anthropic API)
- `asyncpg` (PostgreSQL)
- `pydantic` 2.x (data models; v2 API only — `model_dump`, `model_copy`, `model_validate_json`, `model_json_schema`)
- `tiktoken` (token counting)
- `pydub` + `ffmpeg` (audio processing)
- `mcp` (MCP client)
