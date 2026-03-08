# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Telegram bot that provides access to multiple LLM providers (OpenAI, Anthropic, OpenRouter, local LM Studio) with features like streaming responses, function/tool calling, image generation (DALL-E 3), voice transcription (Whisper), TTS, automatic context summarization, and MCP server integration.

## Running the Project

```bash
docker-compose up -d          # Start all services (app, postgres, image_proxy, pgweb)
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
3. `BatchedInputHandler` (`app/bot/batched_input_handler.py`) — collects user messages into batches (handles text, voice, photos, documents), determines if batch is prompt or context-only
4. `MessageProcessor` (`app/bot/message_processor.py`) — orchestrates a single request: builds context, calls LLM, handles streaming updates, processes function/tool call responses recursively
5. `ContextManager` (`app/context/context_manager.py`) — facade over `DialogManager` and `FunctionManager`
6. `DialogManager` (`app/context/dialog_manager.py`) — loads conversation history from DB, handles sub-dialogues (reply chains), auto-summarizes when context exceeds token limits
7. `ChatGPT` / `AnthropicChatGPT` (`app/openai_helpers/chatgpt.py`, `app/openai_helpers/anthropic_chatgpt.py`) — LLM API interaction, streaming, response parsing

### Multi-Provider LLM Support
- `app/llm_models.py` — defines all models via `LLModel` class with pricing, context config, capabilities, and API client type
- `app/openai_helpers/llm_client.py` — client hierarchy: `BaseLLMClient` → `GenericAsyncOpenAIClient` (OpenAI-compatible APIs) → `OpenAISpecificAsyncOpenAIClient` (OpenAI-specific features like stream usage); `AnthropicAsyncClient` (Anthropic native API)
- `app/openai_helpers/llm_client_factory.py` — creates/caches client instances per model
- To add a new model: add entry in `get_models()` in `llm_models.py` with appropriate client class, capabilities, and context configuration

### Function/Tool Calling
- `app/functions/base.py` — `OpenAIFunction` base class. Subclasses define params via Pydantic `PARAMS_SCHEMA`, implement `run()`, provide `get_description()` and optional `get_system_prompt_addition()`
- `app/openai_helpers/function_storage.py` — `FunctionStorage` registry, converts functions to OpenAI function/tool format
- `app/context/function_manager.py` — decides which functions to register based on settings, user role, and context (e.g., VectorSearch only when documents are in context)
- Built-in functions: `wolframalpha`, `dalle_3`, `todoist`, `obsidian_echo`, `save_user_settings`, `vectara_search`
- MCP integration: `app/functions/mcp/` — dynamically loads tools from configured MCP servers

### Database
- PostgreSQL via `asyncpg`, no ORM
- `app/storage/db.py` — `DB` class with raw SQL queries, `DBFactory` manages connection pool
- Schema in `chatgpttg` schema, tables: `user`, `message`, `completion_usage`, `whisper_usage`, `image_generation_usage`, `tts_usage`
- Messages store full dialog history as JSON with `previous_message_ids` for branching sub-dialogues

### Key Patterns
- **Sub-dialogues**: replying to a message creates a branch — `DialogManager` loads only that branch's history
- **Context expiration**: messages older than `MESSAGE_EXPIRATION_WINDOW` (default 1h) start fresh context
- **Auto-summarization**: when context exceeds `short_term_memory_tokens`, older messages get summarized via LLM
- **Streaming**: responses are streamed to Telegram, editing the message every 2 seconds with cancel button
- **`<think>` tags**: models that output thinking blocks have them parsed, displayed as emoji status during streaming, then stripped before saving
- **User roles**: `UserRole` enum (STRANGER, BASIC, ADVANCED, ADMIN, NOONE) gates access to features and models
- **Image proxy**: `main_image_proxy.py` serves Telegram file IDs as URLs for OpenAI vision API

## Testing

**After any code changes, always run the E2E tests and verify they pass:**

```bash
bash scripts/test.sh
```

The script starts a test PostgreSQL container, runs all tests, and tears down the container. All 20 tests must pass before considering the work done. If a test fails, fix the issue before committing.

Test details: `specs/E2E_TESTS.md`

### Libraries
- `aiogram` 2.x (Telegram bot framework)
- `openai` (OpenAI API)
- `anthropic` (Anthropic API)
- `asyncpg` (PostgreSQL)
- `pydantic` 2.x (data models — but some code uses v1 methods like `.parse_raw()`, `.schema()`)
- `tiktoken` (token counting)
- `pydub` + `ffmpeg` (audio processing)
- `mcp` (MCP client)
