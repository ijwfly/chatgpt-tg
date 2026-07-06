# chatgpt-tg

A self-hosted **Telegram bot** that gives you a single chat interface to multiple LLM
providers — OpenAI, Anthropic (Claude), OpenRouter, and local / OpenAI-compatible endpoints.
It streams responses, calls tools, generates and understands images, transcribes voice, and
can act as an autonomous agent that plans and completes multi-step tasks.

🔥 **Multi-provider** — OpenAI, Anthropic/Claude, OpenRouter, and any OpenAI-compatible API
🔥 **Agent mode** — autonomous multi-step tasks with background sub-agents and live plans
🔥 **MCP integration** — plug in external tools via Model Context Protocol servers
🔥 **Vision + DALL-E 3** — image understanding and image generation out of the box

## 🔑 Key Features

1. **Multi-provider LLMs** — OpenAI, Anthropic (Claude), OpenRouter, and local /
   OpenAI-compatible endpoints (e.g. LM Studio). Switch models from the `/models` menu; add
   your own via `EXTRA_MODELS` without touching the source.
2. **Agent mode** — a proactive agent that completes tasks end-to-end: it plans the work,
   spawns background sub-agents, keeps a live plan updated in the chat, and reports results.
3. **MCP tools** — dynamically load tools from configured MCP servers, with per-server
   access control (minimum role) and custom headers.
4. **Function / tool calling** — the model can call built-in tools when useful: image
   generation (DALL-E 3), WolframAlpha, Todoist, Obsidian Echo, RAG search, and more.
5. **Scheduled tasks** — ask the bot to do something later using natural language
   ("remind me tomorrow at 9"); a scheduler fires it at the right time.
6. **Streaming responses** — answers stream into Telegram in real time, with a cancel
   button, and `<think>` reasoning blocks shown as a live status while the model thinks.
7. **Vision & image generation** — send images for the model to analyze, or ask it to
   generate images with DALL-E 3.
8. **Voice & speech** — voice messages are transcribed (via `gpt-4o-transcribe`) and used as
   input; `/text2speech` turns any message into a voice reply (TTS).
9. **Dynamic dialog management** — the bot manages conversation context automatically; older
   context is summarized when it exceeds the model's limit. You can still `/reset` manually.
10. **Sub-dialogues** — reply to a message to branch off into that thread only, so you can
    juggle multiple conversations in one chat.
11. **Access control** — each user has a role (stranger, basic, advanced, admin) that gates
    access to the bot, model choice, and features. Roles are managed through inline buttons
    sent to an admin chat.
12. **Usage tracking** — per-user API cost tracking (`/usage`, `/usage_all`).

For a deep dive into how everything fits together, see
[`specs/PROJECT_SPEC.md`](specs/PROJECT_SPEC.md) and
[`specs/RUNTIME_ARCHITECTURE.md`](specs/RUNTIME_ARCHITECTURE.md).

## 🔧 Installation

To get the bot up and running:

1. Copy `settings_local.py.example` to `settings_local.py` and fill in your values:
   ```bash
   cp settings_local.py.example settings_local.py
   ```
2. Set `TELEGRAM_BOT_TOKEN` and `OPENAI_TOKEN` in `settings_local.py`.
   (Optional: `ANTHROPIC_TOKEN` for Claude, `OPENROUTER_TOKEN` for OpenRouter.)
3. Set `IMAGE_PROXY_URL` to your server IP / hostname in `settings_local.py`.
4. (optional) Set `USER_ROLE_MANAGER_CHAT_ID` and `ENABLE_USER_ROLE_MANAGER_CHAT = True`
   for access control.
5. (optional) Set the `USER_ROLE_*` variables to your desired defaults.
6. Run `docker-compose up -d` in the root directory of the project.

All settings from `settings.py` can be overridden in `settings_local.py`. This file is
gitignored, so your secrets and environment-specific values are never committed. See
`settings_local.py.example` for the full list of available options.

Database migrations run automatically on Postgres startup, so no manual DB setup is needed.

**Docker Compose overrides**

For development, copy `docker-compose.override.yml.example` to `docker-compose.override.yml`:
```bash
cp docker-compose.override.yml.example docker-compose.override.yml
```
This adds a sleep-loop entrypoint (instead of running the bot), exposes the postgres port,
and starts pgweb. Docker Compose merges the override file automatically.

You can also customize postgres credentials via a `.env` file (see `.env.example`).

**Adding custom LLM models**

You can add extra models without modifying `app/llm_models.py` by setting `EXTRA_MODELS` in
`settings_local.py`:
```python
from app.llm_models import LLModel, LLMPrice, LLMContextConfiguration, LLMCapabilities
from app.openai_helpers.llm_client import OpenAISpecificAsyncOpenAIClient

EXTRA_MODELS = [
    LLModel(
        model_name='my-local-model',
        model_readable_name='My Local Model',
        api_key='not-needed',
        base_url='http://localhost:1234/v1',
        context_configuration=LLMContextConfiguration(
            short_term_memory_tokens=8192,
            summary_length=2048,
            hard_max_context_size=13312,
        ),
        capabilities=LLMCapabilities(
            streaming_responses=True,
        ),
    ),
]
```

This gives you full access to all model parameters: `model_price` (with `LLMPrice`),
`capabilities` (with `LLMCapabilities`), `api_client` (e.g. `OpenAISpecificAsyncOpenAIClient`,
`AnthropicAsyncClient`), `minimum_user_role`, etc.

**Configuring MCP servers**

Register MCP servers in `settings_local.py` to expose their tools to the model. Each server
has its own minimum-role requirement and optional headers:
```python
from settings import MCPServerConfig
from app.storage.user_role import UserRole

MCP_SERVERS = [
    MCPServerConfig(
        url='https://mcp-server.example.com/mcp',
        min_role=UserRole.ADMIN,
        headers={'Authorization': 'Bearer token123'},
    ),
]
```
Use `MCP_SERVERS_AGENT` for servers that should only be available in agent mode.

**Observability (optional)**

Set `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` (and optionally `LANGFUSE_BASE_URL`) to
trace LLM requests with [Langfuse](https://langfuse.com). Tracing is disabled when the keys
are empty.

<details>
<summary>Migrating from dict-based EXTRA_MODELS</summary>

If you used the old dict format, replace dicts with `LLModel(...)` calls and nested dicts
with their dataclass equivalents:

```python
# Old format (still works, but deprecated):
EXTRA_MODELS = [
    {
        'model_name': 'my-model',
        'api_key': 'key',
        'context_configuration': {
            'short_term_memory_tokens': 8192,
            'summary_length': 2048,
            'hard_max_context_size': 13312,
        },
    },
]

# New format:
from app.llm_models import LLModel, LLMContextConfiguration

EXTRA_MODELS = [
    LLModel(
        model_name='my-model',
        api_key='key',
        context_configuration=LLMContextConfiguration(
            short_term_memory_tokens=8192,
            summary_length=2048,
            hard_max_context_size=13312,
        ),
    ),
]
```
</details>

If you've done the optional steps, when you send your first message to the bot you'll get a
management message with your Telegram id and info. Use it to set up your role as admin.

## 🤖 Commands
```
/reset        - reset current dialog
/usage        - show usage for current month
/models       - open models menu
/settings     - open settings menu
/text2speech  - generate voice message from a message (last or replied)
/usage_all    - show usage for all users
```
Most settings live in the `/settings` menu; the commands above are shortcuts for common
actions.

## 🧪 Running Tests

The project has e2e tests that exercise the full message pipeline with real PostgreSQL but
mocked LLM and Telegram APIs.

**Local (recommended for development):**

```bash
./scripts/test.sh -v
```

This starts a test PostgreSQL container, runs pytest on the host, and stops the container
when done. All pytest arguments are forwarded — for example:

```bash
./scripts/test.sh -v -k "test_reset"         # run a specific test
./scripts/test.sh -v --tb=long               # verbose tracebacks
```

**Fully in Docker:**

```bash
./scripts/test_docker.sh
```

Builds the app image, starts PostgreSQL + test runner in Docker, and tears everything down
after. Useful for CI or clean-room runs.

**Manual setup (if you need persistent postgres for debugging):**

```bash
docker compose -f docker-compose.test.yml up -d postgres_test
POSTGRES_HOST=localhost POSTGRES_PORT=15432 pytest tests/ -v
docker compose -f docker-compose.test.yml down
```

See [`specs/E2E_TESTS.md`](specs/E2E_TESTS.md) for details on test architecture and covered
scenarios.

## 🔄 Upgrading from previous versions

<details>
<summary>Migrating from settings.py to settings_local.py</summary>

Previously all configuration was edited directly in `settings.py`, which caused merge
conflicts on every `git pull`. Now your overrides live in `settings_local.py` (gitignored).

**Quick migration:**

```bash
# 1. Your current settings.py becomes your local config
cp settings.py settings_local.py

# 2. Reset settings.py to defaults — no more merge conflicts
git checkout settings.py

# Done! The bot works exactly the same.
```

Optionally, clean up `settings_local.py` by removing unchanged defaults — you can see what
you actually changed with:
```bash
git diff HEAD -- settings_local.py settings.py
```

</details>

<details>
<summary>Migrating custom models from llm_models.py</summary>

If you added custom models directly in `app/llm_models.py`, move them to `EXTRA_MODELS` in
`settings_local.py`. The `LLModel(...)` syntax is identical:

```bash
# See what you changed in llm_models.py
git diff HEAD -- app/llm_models.py
```

Copy your `LLModel(...)` blocks into `settings_local.py`:
```python
from app.llm_models import LLModel, LLMPrice, LLMContextConfiguration, LLMCapabilities
from app.openai_helpers.llm_client import OpenAISpecificAsyncOpenAIClient

EXTRA_MODELS = [
    # paste your LLModel(...) entries here — same syntax as in llm_models.py
]
```

Then reset the file:
```bash
git checkout app/llm_models.py
```

</details>

<details>
<summary>Model list changes</summary>

Deprecated models (gpt-4, gpt-4-turbo, gpt-4o, gpt-4o-mini) were removed from the built-in
list. GPT-4.1 is now the default model — a migration automatically switches all users to it.

If you need any of the removed models, add them back via `EXTRA_MODELS` in
`settings_local.py` (see "Adding custom LLM models" above). All conversations and usage
history are preserved.

</details>

See [`CHANGELOG.md`](CHANGELOG.md) for the full list of changes.

## ⚠️ Troubleshooting

If you have any issues with the bot, please create an issue in this repository. I will try
to help you as soon as possible.

Here are some typical issues and solutions:
- ```Error code: 400 - {'error': {'message': 'Invalid image.', 'type': 'invalid_request_error' ...}}``` — This error usually occurs when OpenAI cannot access the image. Make sure you set up the `IMAGE_PROXY_URL` variable correctly with your server IP / hostname.
You can try to open this url in your browser to check if it works. You can also debug the setup by looking at the `chatgpttg.message` table in postgres — there will be a message with the image url, which you can open in your browser to check.
- ```Error code: 400 - {'error': {'message': 'Invalid content type. image_url is only supported by certain models.', 'type': 'invalid_request_error' ...}}``` — This error usually occurs when you have an image in your context but the current model doesn't support vision. Switch to a vision-capable model or reset your context with the `/reset` command.
