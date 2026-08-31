# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Telegram bot that provides access to multiple LLM providers (OpenAI, Anthropic, OpenRouter, local LM Studio) with features like streaming responses, function/tool calling, image generation (DALL-E 3), voice transcription (Whisper), TTS, automatic context summarization, and MCP server integration.

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
- Built-in functions: `wolframalpha`, `dalle_3`, `save_user_settings`
- Web agents (`app/functions/web_agents.py`, enabled via `ENABLE_WEB_AGENTS` + `TAVILY_API_KEY`): `web_search_agent` and `web_scraper_agent` — each runs an isolated LLM sub-agent (`app/runtime/web_agent_runner.py`, clean context, billed usage) equipped with internal Tavily tools (`tavily_search`/`tavily_extract`, client in `app/web/tavily_client.py`); registered in both `FunctionManager` and `AgentRuntime`
- MCP integration: `app/functions/mcp/` — dynamically loads tools from configured MCP servers

### Bash Sandbox (agent mode)
- `sandbox/` — separate docker-compose service (ubuntu-based, internal network only, no published ports). Per-user isolation via Linux users + `sudo -u`, personal workspace `/workspace/user_<telegram_id>` (chmod 700), lazy provisioning on every request (`ensure_user`). HTTP API: `POST /exec` (bash with process-group-kill timeout), `POST /fileop` (read/write/edit/stat/list/delete via `file_helper.py` under sudo), `GET/PUT /files/{path}` (streaming), `GET /skills` (skills catalog). Paths are confined to the caller's workspace by `resolve_path`; the shared `/workspace/public_skills` is the only exception and only for read-only operations (`allow_public=True`). Caller identified by `X-User-Id` header — trusted internal network, no auth
- `app/sandbox/client.py` — `SandboxClient` (httpx), raises `SandboxError`
- `app/functions/bash_sandbox.py` — agent tools: `bash_exec`, `read_file`, `write_file`, `edit_file`, `send_file_to_chat`. Registered in `AgentRuntime` when `settings.ENABLE_BASH_SANDBOX` (off by default) — so available only with `agent_mode=on`
- Incoming Telegram documents: when user has `agent_mode=on` and sandbox is enabled, `BatchedInputHandler.handle_document_sandbox` saves them into the user's workspace (with agent_mode off documents are not accepted); the agent is notified via a `[file uploaded to agent workspace]` context message. Both the user's document message and the bot's `Saved to agent workspace` confirmation resolve to that context message on reply (via `message_tg_alias`). A document sent with a caption is answered by the agent (`UserInput.force_prompt`) — the caption goes into the same context message; a caption on a forwarded document follows `forward_as_prompt`
- Outgoing files: `send_file_to_chat` downloads from the sandbox and sends via the `send_document` side effect (`SideEffectHandler` protocol + `TelegramSideEffectHandler`); it sets `result_tg_message_id`, so the tool response row carries the document's tg id and a reply to the file continues the dialog branch

### Skills (agent mode)
- A skill is a folder with `SKILL.md` (YAML frontmatter `name` + `description`, then markdown instructions) and optional `reference/`, `scripts/`, `templates/`. Format is compatible with Claude Code Agent Skills
- Two locations: personal `skills/` inside the user's workspace (agent writes them itself) and shared read-only `/workspace/public_skills`. Built-in skills live in `sandbox/skills/` in the repo, are `COPY`-ed into the image and re-synced into `public_skills` by `sandbox/entrypoint.sh` on every container start (bundled ones are overwritten, admin-added ones are left alone). `sandbox/skills/skill-creator` is the bundled skill that teaches the agent to write skills; it ships `scripts/validate_skill.py`
- Only the **catalog** reaches the LLM: `AgentRuntime` calls `get_skills_prompt_addition()` (`app/skills/catalog.py`) once per turn, which hits `GET /skills` and renders a `## Skills` block (name, description, path) appended to the system prompt and to the sub-agent prompt. Bodies are read by the model itself with `read_file` — no dedicated tools. A personal skill shadows a shared one with the same name; the list is capped by `SKILLS_MAX_COUNT` / `SKILLS_MAX_DESCRIPTION_CHARS`; a sandbox failure only logs a warning and drops the block
- Gated by `ENABLE_SKILLS` + `ENABLE_BASH_SANDBOX` + `agent_mode`

### Database
- PostgreSQL via `asyncpg`, no ORM
- `app/storage/db.py` — `DB` class with raw SQL queries, `DBFactory` manages connection pool
- Schema in `chatgpttg` schema, tables: `user`, `message`, `completion_usage`, `whisper_usage`, `image_generation_usage`, `tts_usage`
- Messages store full dialog history as JSON with `previous_message_ids` for branching sub-dialogues

### Key Patterns
- **Sub-dialogues**: replying to a message creates a branch — `DialogManager` loads only that branch's history. Chat messages without a context row of their own (upload confirmations, the user's voice message behind a transcription) are registered as aliases in `message_tg_alias`, so replying to them resolves to the right row
- **Context expiration**: messages older than `MESSAGE_EXPIRATION_WINDOW` (default 1h) start fresh context
- **Auto-summarization**: when context exceeds `short_term_memory_tokens`, older messages get summarized via LLM
- **Rich messages**: LLM answers, `/usage`, `/models` and admin cards are sent as Telegram Rich Messages (`sendRichMessage` with `InputRichMessage(markdown=...)`, GFM markdown, 32768-char limit) via `app/bot/rich_messages.py`; a rejected markup falls back to plain `sendMessage`. Service texts (errors, confirmations, transcriptions, verbose tool output) stay plain via `utils.send_telegram_message`. See `specs/RICH_MESSAGES.md`
- **Streaming**: by default a real rich message is created and edited every second with an inline Stop button (`ChatServiceMessage`). With `settings.RICH_DRAFT_STREAMING = True` (off by default — clients render drafts poorly) private chats stream an ephemeral rich draft (`sendRichMessageDraft`, `DraftStream` in `app/bot/service_message.py`, thinking/tool hints as `<tg-thinking>`, native Stop button via `can_stop`), finished with a fresh `sendRichMessage`; groups (or a failed draft call) fall back to the edit path. The Bot API 10.3 `stopped_message_generation` update is handled by `CancellationManager.process_stopped_generation` (registered on `dp.stopped_message_generation`, aiogram ≥ 3.31)
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
