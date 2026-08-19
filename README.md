# chatgpt-tg

A self-hosted **Telegram bot** that gives you a single chat interface to multiple LLM
providers — OpenAI, Anthropic (Claude), OpenRouter, and local / OpenAI-compatible endpoints.
It streams responses, calls tools, generates and understands images, transcribes voice, and
can act as an autonomous agent that plans and completes multi-step tasks.

🔥 **Multi-provider** — OpenAI, Anthropic/Claude, OpenRouter, and any OpenAI-compatible API
🔥 **Agent mode** — autonomous multi-step tasks with background sub-agents and live plans
🔥 **Skills** — teach the agent a workflow once; it stores it and loads it when it fits
🔥 **MCP integration** — plug in external tools via Model Context Protocol servers
🔥 **Vision + DALL-E 3** — image understanding and image generation out of the box

## 🔑 Key Features

1. **Multi-provider LLMs** — OpenAI, Anthropic (Claude), OpenRouter, and local /
   OpenAI-compatible endpoints (e.g. LM Studio). Switch models from the `/models` menu; add
   your own via `EXTRA_MODELS` without touching the source.
2. **Agent mode** — a proactive agent that completes tasks end-to-end: it plans the work,
   spawns background sub-agents, keeps a live plan updated in the chat, and reports results.
   With the bash sandbox enabled it can also run shell commands, work with files, and
   exchange documents with the chat.
3. **Web search & scraping** — `web_search_agent` and `web_scraper_agent` tools, each running
   an isolated LLM sub-agent powered by [Tavily](https://tavily.com). Available in both
   normal and agent mode.
4. **Bash sandbox** — an isolated per-user execution environment (separate Docker service,
   internal network only) where the agent runs bash, edits files, and receives documents you
   upload to the chat.
5. **Skills** — folders of instructions the agent loads only when they fit the task. Ask it
   to "make a skill" for something you do regularly and it writes one into its sandbox; from
   the next message the skill is in its catalog and it follows your process by itself.
6. **MCP tools** — dynamically load tools from configured MCP servers, with per-server
   access control (minimum role) and custom headers.
7. **Function / tool calling** — the model can call built-in tools when useful: image
   generation (DALL-E 3), WolframAlpha, and more.
8. **Scheduled tasks** — ask the bot to do something later using natural language
   ("remind me tomorrow at 9"); a scheduler fires it at the right time.
9. **Streaming responses** — answers stream into Telegram in real time, with a cancel
   button, and `<think>` reasoning blocks shown as a live status while the model thinks.
10. **Vision & image generation** — send images for the model to analyze, or ask it to
   generate images with DALL-E 3.
11. **Voice & speech** — voice messages and video notes are transcribed (via
    `gpt-4o-transcribe`) and used as input; `/text2speech` turns any message into a voice
    reply (TTS).
12. **Dynamic dialog management** — the bot manages conversation context automatically; older
    context is summarized when it exceeds the model's limit. You can still `/reset` manually.
13. **Sub-dialogues** — reply to a message to branch off into that thread only, so you can
    juggle multiple conversations in one chat.
14. **Access control** — each user has a role (stranger, basic, advanced, admin) that gates
    access to the bot, model choice, and features. Roles are managed through inline buttons
    sent to an admin chat.
15. **Usage tracking** — per-user API cost tracking (`/usage`, `/usage_all`).

For a deep dive into how everything fits together, see
[`specs/PROJECT_SPEC.md`](specs/PROJECT_SPEC.md) and
[`specs/RUNTIME_ARCHITECTURE.md`](specs/RUNTIME_ARCHITECTURE.md).

## 🚀 Quick start (minimal setup)

The shortest path to a working bot — three steps:

1. Copy the local settings template (it is gitignored, so your secrets are never committed):
   ```bash
   cp settings_local.py.example settings_local.py
   ```
2. Fill in three values in `settings_local.py`:
   - `TELEGRAM_BOT_TOKEN` — get one from [@BotFather](https://t.me/BotFather)
   - `OPENAI_TOKEN` — your OpenAI API key
     (optional: `ANTHROPIC_TOKEN` for Claude, `OPENROUTER_TOKEN` for OpenRouter)
   - `IMAGE_PROXY_URL` — your server's IP / hostname. Needed for vision: OpenAI fetches
     the images you send through this proxy.
3. Start everything:
   ```bash
   docker-compose up -d
   ```

That's it. Database migrations run automatically on Postgres startup — no manual DB setup.
Out of the box you get streaming responses, vision, DALL-E 3 image generation, voice / video
note transcription, TTS, automatic context summarization, and scheduled tasks.

**Recommended: set up access control.** With the defaults, anyone who finds your bot can use
it (and spend your API credits). Add to `settings_local.py`:

```python
ENABLE_USER_ROLE_MANAGER_CHAT = True
USER_ROLE_MANAGER_CHAT_ID = -100123456789   # chat where role-management messages go
USER_ROLE_DEFAULT = UserRole.STRANGER       # new users get no access until approved
```

When a new user messages the bot, the manager chat receives a message with their Telegram id
and inline buttons to assign a role (stranger / basic / advanced / admin). Send your bot a
first message and use it to make yourself admin.

Any setting from `settings.py` can be overridden in `settings_local.py` — see
`settings_local.py.example` for the most common options.

## 🤖 Full agent setup

Agent mode is enabled by default (`ENABLE_AGENT_RUNTIME = True`); each user switches it on in
the `/settings` menu. On its own it gives the model planning tools and background
sub-agents. The options below — all in `settings_local.py` — turn it into a fully functional
agent with web access and an execution environment.

**1. Web search & scraping (Tavily)**

```python
ENABLE_WEB_AGENTS = True
TAVILY_API_KEY = 'tvly-...'   # get a key at https://tavily.com
```

Adds two tools: `web_search_agent` (searches the web and returns a digest with sources) and
`web_scraper_agent` (reads a specific page and extracts what you asked for). Each runs as an
isolated LLM sub-agent with its own clean context. Works in normal mode too, not just agent
mode. Optionally set `WEB_AGENT_MODEL` to run these sub-agents on a specific model (empty =
the user's current model).

**2. Bash sandbox**

```python
ENABLE_BASH_SANDBOX = True
```

The `sandbox` service is already part of `docker-compose.yml`, so it starts together with
everything else — you only need the flag. It is an isolated Ubuntu container on the internal
Docker network (no published ports) with per-user Linux accounts and private workspaces.

With the sandbox enabled, the agent gets `bash_exec`, `read_file` / `write_file` /
`edit_file`, and `send_file_to_chat` tools, and documents you upload to the chat are saved
into the agent's workspace (agent mode must be on for document uploads).

**3. Skills**

```python
ENABLE_SKILLS = True  # default; needs ENABLE_BASH_SANDBOX
```

A skill is a folder with a `SKILL.md` (frontmatter `name` + `description`, then instructions)
and optional `reference/`, `scripts/`, `templates/`. Only the catalog — name, description and
path — goes into the agent's system prompt; the agent opens a skill's body itself when the
description matches the task, so you can have many skills without bloating the prompt. The
format matches Claude Code Agent Skills, so skills are portable.

Personal skills live in `skills/` in the user's sandbox workspace and are created by the agent
itself — the bundled `skill-creator` skill walks it through writing one and validating it with
`scripts/validate_skill.py`. Shared read-only skills live in `/workspace/public_skills`: put a
folder in `sandbox/skills/` and rebuild the sandbox image (`docker-compose up -d --build
sandbox`), or drop one straight into the `sandbox_workspace` volume under `public_skills/`.

**4. MCP servers**

Register MCP servers to expose their tools to the model. Each server has its own
minimum-role requirement and optional headers:
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

**5. Scheduled tasks timezone**

Scheduled tasks work out of the box, but set your users' IANA timezone so "tomorrow at 10am"
resolves in the right zone (default is UTC):
```python
USER_TIMEZONE = 'Europe/Moscow'
```

**Putting it together** — a complete `settings_local.py` for a full-featured agent:

```python
from app.storage.user_role import UserRole

TELEGRAM_BOT_TOKEN = '...'
OPENAI_TOKEN = 'sk-...'
ANTHROPIC_TOKEN = 'sk-ant-...'          # optional
IMAGE_PROXY_URL = 'http://1.2.3.4'

ENABLE_USER_ROLE_MANAGER_CHAT = True
USER_ROLE_MANAGER_CHAT_ID = -100123456789
USER_ROLE_DEFAULT = UserRole.STRANGER

ENABLE_WEB_AGENTS = True
TAVILY_API_KEY = 'tvly-...'

ENABLE_BASH_SANDBOX = True

USER_TIMEZONE = 'Europe/Moscow'
```

## ⚙️ Advanced configuration

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
