# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-07-06 — "Agent Mode"

This is a major release. The bot grew from an OpenAI-only chat proxy into a
**multi-provider, agentic** Telegram assistant. It gains native Anthropic/Claude
support, an autonomous agent runtime with background sub-agents and live plans, MCP
tool integration, scheduled tasks, and a full end-to-end test suite. The configuration
model also changed in a **backwards-incompatible** way — see "Migration" below before
upgrading.

### Added

- **Agent mode** — a new `AgentRuntime` that completes complex tasks end-to-end:
  autonomous multi-step execution, background sub-agents, and plan management with live
  plan progress rendered in the Telegram chat. Configurable via `AGENT_SYSTEM_PROMPT`,
  `ENABLE_AGENT_RUNTIME`, `AGENT_MAX_ITERATIONS`, `AGENT_SUB_AGENT_MAX_ITERATIONS`,
  `AGENT_BG_TASK_TIMEOUT`, and `AGENT_PLAN_REMINDER_INTERVAL`.
  (`app/runtime/agent_runtime.py`, `background_task_manager.py`, `plan_manager.py`,
  `app/functions/agent_tools.py`)
- **Anthropic / Claude support** — native Anthropic API client with streaming and tool
  calling. Add your key via `ANTHROPIC_TOKEN`.
  (`app/openai_helpers/anthropic_chatgpt.py`, `AnthropicAsyncClient`)
- **MCP (Model Context Protocol) integration** — dynamically load tools from configured
  MCP servers, with per-server minimum-role access control and custom headers. Separate
  server lists for normal (`MCP_SERVERS`) and agent-only (`MCP_SERVERS_AGENT`) modes, plus
  a per-call timeout (`MCP_TOOL_CALL_TIMEOUT`). (`app/functions/mcp/`)
- **Scheduled tasks** — schedule messages/tasks with natural-language date parsing; a
  scheduler service polls and fires them. (`app/bot/scheduler_service.py`,
  `SCHEDULER_POLL_INTERVAL`, migration `0015`)
- **Langfuse observability** — optional LLM request tracing via Langfuse. Enable by setting
  `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` (and optional `LANGFUSE_BASE_URL`).
  (`app/runtime/langfuse_utils.py`)
- **Per-model `extra_completion_params`** — forward arbitrary kwargs to
  `chat.completions.create()` for a given model (e.g. `extra_body` for vLLM / SGLang
  vendor-specific params). Runtime values set by the bot take precedence.
- **Transient function call hints** — live "calling function…" hints shown as service
  messages in the chat during a tool-calling turn. (`app/bot/service_message.py`,
  migration `0016`)
- **Current date in system prompt** — the model is told today's date for time-aware answers.
- **Transport-agnostic LLM Runtime layer** — extracted the LLM loop out of the Telegram
  bot into a reusable `LLMRuntime` protocol with typed `RuntimeEvent`s and `UserInput`
  types, a `DefaultLLMRuntime`, and side-effect handlers. See
  `specs/RUNTIME_ARCHITECTURE.md`. (`app/runtime/`)
- **`<think>` reasoning blocks** — models that emit thinking blocks have them parsed and
  shown as an emoji status while streaming, then stripped before the response is saved.
- **Todoist integration** (admin) — add Todoist tasks from chat
  (`ENABLE_TODOIST_ADMIN_INTEGRATION`, `TODOIST_TOKEN`). (`app/functions/todoist.py`)
- **Obsidian Echo integration** (admin) — push notes to an Obsidian vault
  (`ENABLE_OBSIDIAN_ECHO_ADMIN_INTEGRATION`, `OBSIDIAN_ECHO_BASE_URL`,
  `OBSIDIAN_ECHO_VAULT_TOKEN`). (`app/functions/obsidian_echo.py`)
- **Custom models via settings** — define extra LLM models in `settings_local.py` using
  `EXTRA_MODELS` without editing `app/llm_models.py`.
- **Configurable image input format** — per-model `image_input_format` (`url` or `base64`)
  for vision requests.
- **End-to-end test infrastructure** — full message-pipeline tests against a real
  PostgreSQL with mocked LLM and Telegram APIs (~23 tests). Run with `scripts/test.sh`.
  See `specs/E2E_TESTS.md`. (`tests/`, `docker-compose.test.yml`, `pytest.ini`)
- **Project documentation** — `CLAUDE.md`, `specs/PROJECT_SPEC.md`, and
  `specs/RUNTIME_ARCHITECTURE.md`.

### Changed

- **Configuration model (breaking)** — overrides now live in a gitignored
  `settings_local.py` instead of editing `settings.py` directly. `settings.py` holds
  defaults and ends with `from settings_local import *`. See `settings_local.py.example`.
- **`EXTRA_MODELS` format (breaking)** — extra models are now `LLModel(...)` dataclass
  instances (the old dict format is still accepted as legacy).
- **Model list overhaul (breaking)** — deprecated models (`gpt-4`, `gpt-4-turbo`,
  `gpt-4o`, `gpt-4o-mini`) were removed from the built-in list and **GPT-4.1 is now the
  default**. A database migration automatically switches all users to GPT-4.1; conversation
  and usage history are preserved. Removed models can be re-added via `EXTRA_MODELS`.
  (migrations `0012`–`0014`)
- **New `MCPServerConfig` config type** and removed `OPENAI_CHAT_COMPLETION_TEMPERATURE`.
- **Voice transcription** switched to the `gpt-4o-transcribe` model.
- **Docker** — added `docker-compose.override.yml.example` (dev entrypoint, exposed
  Postgres, pgweb) and `docker-compose.test.yml`; postgres credentials configurable via
  `.env`.
- **Dependency upgrades (breaking)** — `pydantic 1.10 → 2.11`, `openai 1.3 → 1.35`, plus
  new dependencies: `anthropic`, `mcp`, `todoist_api_python`, `dateparser`, `croniter`,
  `langfuse`, `pytest`/`pytest-asyncio`, and upgrades to `aiohttp`/`fastapi`/`uvicorn`/`httpx`.
- Removed the explicit `temperature` parameter from all completion calls.

### Fixed

- Streaming tool calls: content lost when `tool_calls` follow text; tool calls lost on the
  final usage-only chunk; assistant `tool_call` message not saved when content is present;
  improved tool-call accumulation in streaming responses.
- `<think>` tags leaking into visible text when a `tool_call` interrupts thinking.
- Function-call errors are now caught and returned to the LLM for retry.
- MCP tool calls have a timeout to prevent infinite hangs.
- Usage and price reporting fixed for removed models (price is stored in the DB).
- Context-splitting fix for summarization when the context exceeds the model limit.
- base64 image fetching fixed via an image-proxy URL fallback.
- Batched input error handling fixed.
- Function-call logging cleanup — one service message reused across an LLM turn, and
  `status_message` resolved in the agent runtime too.

### Migration

Upgrading from v1.x requires moving your configuration:

```bash
# 1. Turn your current settings.py into your local overrides
cp settings.py settings_local.py

# 2. Reset settings.py to defaults (no more merge conflicts on pull)
git checkout settings.py
```

If you added custom models directly in `app/llm_models.py`, move them into `EXTRA_MODELS`
in `settings_local.py` and `git checkout app/llm_models.py`. Database migrations
(including the GPT-4.1 switch) run automatically on Postgres startup. See the
"Upgrading from previous versions" section of the README for details.

## [1.6.0] - 2024-05-13

Last release before the 2.0.0 rewrite. See the git history for changes up to this point.

[2.0.0]: https://github.com/ijwfly/chatgpt-tg/compare/v1.6.0...v2.0.0
[1.6.0]: https://github.com/ijwfly/chatgpt-tg/releases/tag/v1.6.0
