# chatgpt-tg — Project Specification

> Self-hosted Telegram bot for communicating with multiple LLM providers

---

## 1. General Description

**chatgpt-tg** — a self-hosted Telegram bot that proxies user conversations to multiple LLM providers. The bot supports multimodal input (text, images, voice, documents), image generation, text-to-speech, a plugin system via function calling and MCP, as well as RAG via Vectara.

**Target audience:** personal use, small teams, self-hosted operators.

**Technology stack:**
- Language: Python 3.11
- Telegram framework: aiogram 2.25.1
- LLM SDK: openai 1.35.8, anthropic 0.29.0, mcp 1.13.0
- Database: PostgreSQL 15.3 (asyncpg 0.27.0)
- Web: FastAPI 0.116.1 + uvicorn (image proxy)
- Containerization: Docker, Docker Compose
- Audio: pydub + ffmpeg
- Tokenization: tiktoken 0.7.0

---

## 2. System Architecture

### 2.1 Infrastructure Diagram

```
                         ┌─────────────────┐
                         │  Telegram API    │
                         │   (external)     │
                         └────────┬─────────┘
                                  │ polling
                                  ▼
┌─────────────────────────────────────────────────────────┐
│                    Docker Compose                        │
│                                                          │
│  ┌──────────────┐    ┌──────────────┐   ┌────────────┐  │
│  │     app       │───▶│  PostgreSQL   │◀──│   pgweb    │  │
│  │   main.py     │    │    :5432     │   │   :8081    │  │
│  │  (bot core)   │    └──────────────┘   └────────────┘  │
│  └──────┬───────┘                                        │
│         │                                                │
│  ┌──────┴───────┐    ┌──────────────────┐                │
│  │ image_proxy   │    │ update_keyboards │                │
│  │ FastAPI :8321 │    │   (one-shot)     │                │
│  └──────────────┘    └──────────────────┘                │
└─────────────────────────────────────────────────────────┘
         │
         ▼ HTTP
┌─────────────────────────────────────────────┐
│            External Services (API)           │
│                                              │
│  • OpenAI (Chat, DALL-E 3, Whisper, TTS)    │
│  • Anthropic (Claude)                        │
│  • OpenRouter (third-party models)           │
│  • WolframAlpha (optional)                   │
│  • Todoist (optional, admin)                 │
│  • Obsidian Echo (optional, admin)           │
│  • Vectara (optional, RAG)                   │
│  • MCP Servers (optional, dynamic tools)     │
└─────────────────────────────────────────────┘
```

### 2.2 Internal Application Architecture

```
app/
├── bot/               ← Telegram transport layer
│   │                    Handlers, middleware, UI menus, runtime adapter
│   │
├── runtime/           ← LLM Runtime layer (transport-agnostic)
│   │                    Runtime protocol, events, user input types,
│   │                    DefaultLLMRuntime, side effects protocol
│   │
├── context/           ← Context orchestration (transport-agnostic)
│   │                    Conversation management, function aggregation
│   │
├── openai_helpers/    ← LLM client abstraction layer
│   │                    Clients, tokenization, utilities
│   │
├── functions/         ← Plugin system (transport-agnostic)
│   │                    Base class, built-in functions, MCP
│   │
├── storage/           ← Data access layer
│   │                    PostgreSQL queries, roles, Vectara
│   │
└── llm_models.py      ← Model registry
```

**Dependency direction:**
```
bot → runtime → context → openai_helpers → storage
                        ↘ functions ↗
```

> See [RUNTIME_ARCHITECTURE.md](RUNTIME_ARCHITECTURE.md) for detailed runtime layer documentation, including how to add new runtimes and transports.

### 2.3 Key Design Patterns

| Pattern | Implementation | File | Purpose |
|---------|---------------|------|---------|
| Protocol (DI) | `LLMRuntime` | `runtime/runtime.py` | Pluggable LLM execution layer |
| Protocol (DI) | `SideEffectHandler` | `runtime/side_effects.py` | Transport-agnostic side effects for functions |
| Event-based AsyncGenerator | `RuntimeEvent` hierarchy | `runtime/events.py` | Decoupled streaming between runtime and transport |
| Factory | `LLMClientFactory` | `openai_helpers/llm_client_factory.py` | Lazy creation and caching of LLM clients |
| Abstract Base + Plugin | `OpenAIFunction` | `functions/base.py` | All tool functions inherit from this ABC |
| Middleware | `UserMiddleware` | `bot/user_middleware.py` | Injection of `User` object into each handler |
| Registry | `FunctionStorage` | `openai_helpers/function_storage.py` | Function registry for LLM tool-calling |
| Batching/Debounce | `BatchedInputHandler` | `bot/batched_input_handler.py` | 300ms batching of multi-messages |
| Cancellation Token | `CancellationManager` | `bot/cancellation_manager.py` | Cooperative cancellation of streaming responses |

---

## 3. Message Processing Pipeline

### 3.1 Main Flow

```
User sends message(s) to Telegram
                    │
                    ▼
┌──────────────────────────────────────────────┐
│  UserMiddleware.on_pre_process_message        │
│  • get_or_create_user() from DB              │
│  • Access check (USER_ROLE_BOT_ACCESS)       │
│  • Inject user into handler data             │
│  • Notify admin about new user               │
└──────────────────────┬───────────────────────┘
                       ▼
┌──────────────────────────────────────────────┐
│  BatchedInputHandler.handle()                 │
│  • Accumulate messages in user-specific batch │
│  • Timer.sleep(300ms) — wait for more msgs   │
│  • Lock-protected batch extraction            │
└──────────────────────┬───────────────────────┘
                       ▼
┌──────────────────────────────────────────────┐
│  BatchedInputHandler.process_batch()          │
│  • Sort by message_id                        │
│  • Transport preprocessing (builds UserInput):│
│    voice/audio → Whisper → VoiceTranscription │
│    document → Vectara upload → DocumentInput  │
│    photo → ImageInput (file_id + dimensions)  │
│    text → TextInput                           │
│    forwarded → TextInput (with attribution)   │
│  • batch_is_prompt()?                        │
│    No  → add_context_only(user_input)        │
│    Yes → TypingWorker + process(user_input)  │
└──────────────────────┬───────────────────────┘
                       ▼
┌──────────────────────────────────────────────┐
│  MessageProcessor.process()                   │
│  • Build ConversationSession from aiogram msg │
│  • Create ContextManager (loads dialog history│
│    from DB, collects available functions)      │
│  • Create DefaultLLMRuntime + adapter         │
│  • Delegate to TelegramRuntimeAdapter         │
└──────────────────────┬───────────────────────┘
                       ▼
┌──────────────────────────────────────────────┐
│  TelegramRuntimeAdapter.handle_turn()         │
│  Consumes RuntimeEvent stream from runtime:   │
│  • StreamingContentDelta → throttled editing, │
│    thinking emoji, cancel button              │
│  • FinalResponse → split by 4080 chars,      │
│    send/edit final msg, save to context       │
│  • FunctionCallCompleted → verbose display    │
└──────────────────────┬───────────────────────┘
                       ▼
┌──────────────────────────────────────────────┐
│  DefaultLLMRuntime.process_turn()             │
│  1. Add UserInput to context                  │
│  2. Select LLM client (ChatGPT / Anthropic)  │
│  3. Build system prompt + function storage    │
│  4. Stream LLM response → yield deltas       │
│  5. yield FinalResponse                       │
│  6. Tool calls → execute → yield events       │
│     → pass results to LLM → recurse to 4     │
│     (up to SUCCESSIVE_FUNCTION_CALLS_LIMIT)   │
│  7. Usage tracking (tokens, price) to DB      │
└──────────────────────────────────────────────┘
```

> For full runtime architecture details, see [RUNTIME_ARCHITECTURE.md](RUNTIME_ARCHITECTURE.md).

### 3.2 Response Streaming

Implemented in `TelegramRuntimeAdapter.handle_turn()` (consumes `StreamingContentDelta` events from the runtime):

1. **First chunk** — a new Telegram message is created with an inline Cancel button
2. **Subsequent chunks** — message updates no more often than once every **2 seconds** (`WAIT_BETWEEN_MESSAGE_UPDATES`)
3. **Length limit** — when exceeding **4080 characters**, updates stop and "⏳..." is appended
4. **Cancellation** — on Cancel press: stream is closed, 20 tokens added to usage
5. **Skip small updates** — content under 50 characters is not displayed

### 3.3 Context Window Management

Implemented in `DialogManager`:

```
hard_max_context_size          ← safety limit, ValueError on overflow
  └── short_term_memory_tokens ← threshold for auto-summarization
        └── summary_length     ← max length of generated summary
```

**Auto-summarization algorithm:**
1. If `count_tokens(messages) >= short_term_memory_tokens`:
2. Find split point at the middle of context (by token count)
3. Left half → summarization via a separate LLM call
4. Summary is saved to DB as `MessageType.SUMMARY`
5. New context = `[summary] + [right half]`

> **Note:** for Anthropic models, summarization is always performed via GPT-4o (cross-provider fallback).

**Conversation expiration:**
- `MESSAGE_EXPIRATION_WINDOW` = 3600 seconds (1 hour) by default
- If the last message is older than this window — a new conversation starts
- `activation_dtime` is updated when replying to a specific message (reply)

---

## 4. Database Schema

### 4.1 ER Diagram

```
chatgpttg.user                         chatgpttg.message
┌───────────────────────────┐          ┌────────────────────────────┐
│ id (bigserial PK)         │◀─────────│ user_id (bigint FK)        │
│ telegram_id (bigserial)   │          │ id (bigserial PK)          │
│ current_model (text)      │          │ message (jsonb)            │
│ gpt_mode (text)           │          │ cdate (timestamptz)        │
│ forward_as_prompt (bool)  │          │ activation_dtime (tz)      │
│ voice_as_prompt (bool)    │          │ previous_message_ids (int[])│
│ use_functions (bool)      │          │ tg_chat_id (bigint)        │
│ auto_summarize (bool)     │          │ tg_message_id (bigint)     │
│ full_name (text)          │          │ message_type (enum)        │
│ username (text)           │          └────────────────────────────┘
│ role (user_roles enum)    │
│ streaming_answers (bool)  │          chatgpttg.completion_usage
│ function_call_verbose(bool)│          ┌────────────────────────────┐
│ image_generation (bool)   │◀─────────│ user_id (bigserial FK)     │
│ tts_voice (text)          │          │ id (bigserial PK)          │
│ system_prompt_settings(txt)│          │ prompt_tokens (int)        │
│ system_prompt_settings_   │          │ completion_tokens (int)    │
│   enabled (bool)          │          │ total_tokens (int)         │
│ cdate (timestamptz)       │          │ model (text)               │
└───────────────────────────┘          │ cdate (timestamptz)        │
                                       │ price (numeric)            │
chatgpttg.whisper_usage                └────────────────────────────┘
┌────────────────────────┐
│ id, user_id, audio_sec │             chatgpttg.image_generation_usage
│ cdate, price           │             ┌────────────────────────────┐
└────────────────────────┘             │ id, user_id, model         │
                                       │ resolution, cdate, price   │
chatgpttg.tts_usage                    └────────────────────────────┘
┌────────────────────────┐
│ id, user_id, model     │
│ characters_count       │
│ cdate, price           │
└────────────────────────┘
```

### 4.2 Enum Types

```sql
chatgpttg.message_types: ('message', 'summary', 'reset', 'document')
chatgpttg.user_roles:    ('admin', 'advanced', 'basic', 'stranger')
```

### 4.3 Message Storage Strategy

- **`previous_message_ids`** — PostgreSQL `BIGINT[]` array storing the IDs of all preceding messages in the conversation chain
- When creating a new message (`create_message`), IDs of all previous messages are taken from `previous_messages`
- **Reply threading**: replying to a specific Telegram message → branching from that message's chain (`get_telegram_message`)
- **`activation_dtime`** — time of last interaction; messages older than `MESSAGE_EXPIRATION_WINDOW` → new conversation
- **`message`** — full `DialogMessage` in JSON format (role, content, function_call, tool_calls, tool_call_id)

### 4.4 Migrations

| # | File | Purpose |
|---|------|---------|
| 0000 | `0000_init.sql` | Initial schema (user, message) |
| 0001 | `0001_add_usage.sql` | API usage tracking tables |
| 0002 | `0002_add_dynamic_dialogs.sql` | Message threading (previous_message_ids) |
| 0003 | `0003_add_user_roles.sql` | Role-based access control |
| 0004 | `0004_add_streaming_answers.sql` | Streaming settings |
| 0005 | `0005_add_image_generation_usage.sql` | Image generation tracking |
| 0006 | `0006_add_user_image_generation_setting.sql` | User image generation setting |
| 0007 | `0007_add_tts_usage_and_settings.sql` | TTS usage and voice settings |
| 0008 | `0008_add_user_system_prompt_settings.sql` | User system prompt settings |
| 0009 | `0009_add_message_type_document.sql` | Document message type |
| 0010 | `0010_gpt_4_turbo_alias.sql` | GPT-4 Turbo alias |
| 0011 | `0011_gpt_4_turbo_release.sql` | GPT-4 Turbo release model |
| 0012 | `0012_add_price_to_usage.sql` | Price field in usage tables |

> Migrations are forward-only — no rollback mechanism exists.

---

## 5. LLM Integration

### 5.1 Client Hierarchy

```
BaseLLMClient (abstract)                    llm_client.py
│   api_key, base_url
│   chat_completions_create()
│
├── GenericAsyncOpenAIClient                llm_client.py
│   │ OpenAI SDK (openai.AsyncOpenAI)
│   │ For OpenAI-compatible APIs (OpenRouter)
│   │
│   └── OpenAISpecificAsyncOpenAIClient     llm_client.py
│       Adds stream_options: {include_usage: true}
│       For native OpenAI API
│
└── AnthropicAsyncClient                    llm_client.py
    anthropic.AsyncClient
    System prompt extraction, max_tokens=4096
```

`LLMClientFactory` caches one client instance per `model_name`.

### 5.2 Model Registry

| Constant | Readable Name | Provider | API Client | Capabilities | Min Role | Context (STM/Sum/Hard) |
|----------|--------------|----------|-----------|-------------|---------|----------------------|
| `GPT_35_TURBO` | GPT-3.5 | OpenAI | OpenAISpecific | FC, TC, Stream | BOT_ACCESS | 10K / 2K / 13K |
| `GPT_4_TURBO` | GPT-4 Turbo | OpenAI | OpenAISpecific | FC, TC, IMG, Stream | CHOOSE_MODEL | 5K / 2K / 13K |
| `GPT_4O` | GPT-4o | OpenAI | OpenAISpecific | FC, TC, IMG, Stream | CHOOSE_MODEL | 8K / 2K / 13K |
| `GPT_4O_MINI` | GPT-4o mini | OpenAI | OpenAISpecific | FC, TC, IMG, Stream | CHOOSE_MODEL | 8K / 2K / 13K |
| `ANTHROPIC_CLAUDE_35_SONNET` | Claude 3.5 Sonnet | Anthropic | AnthropicAsync | FC, TC, IMG, Stream | CHOOSE_MODEL | 10K / 2K / 15K |
| `OPENROUTER_WIZARDLM2` | WizardLM-2 8x22b | OpenRouter | Generic | Stream | CHOOSE_MODEL | 8K / 2K / 13K |

**Capabilities:** FC = function_calling, TC = tool_calling, IMG = image_processing, Stream = streaming_responses

**Deprecated models** (role = `NOONE`, unavailable to users):
- GPT-3.5 16K, GPT-4, GPT-4 Turbo Preview, GPT-4 Vision Preview

**Pricing** (per 1000 tokens):

| Model | Input | Output |
|-------|-------|--------|
| GPT-3.5 | $0.0005 | $0.0015 |
| GPT-4 Turbo | $0.01 | $0.03 |
| GPT-4o | $0.005 | $0.015 |
| GPT-4o mini | $0.00015 | $0.0006 |
| Claude 3.5 Sonnet | $0.003 | $0.015 |
| WizardLM-2 | $0.00065 | $0.00065 |

### 5.3 Anthropic Adapter

File `openai_helpers/anthropic_chatgpt.py` — translation layer from OpenAI format to Anthropic API:

- **System prompt**: extracted from the messages array and passed as a separate `system=` parameter
- **Role mapping**: `tool` → `user` (Anthropic does not support the tool role)
- **Images**: URL → base64 via image proxy (with caching via `@alru_cache`)
- **Tool use**: conversion of OpenAI tool schema → Anthropic `input_schema`
- **Message merging**: combining consecutive messages with the same role (Anthropic API requirement)
- **Streaming events**: handling `message_start`, `content_block_start`, `content_block_delta`, `content_block_stop`, `message_delta`, `message_stop`, `ping`

### 5.4 Token Counting

File `openai_helpers/count_tokens.py`:

- **OpenAI models**: `tiktoken` with model-specific encoders
- **Anthropic**: fallback to GPT-4 tokenizer (approximate)
- **Unknown models**: fallback to `len(str)` (character count)
- **Image tokens**: using the OpenAI high-detail formula:
  - Low detail: 85 tokens
  - High detail: scaling to 2048px, splitting into 512×512 tiles → `tiles × 170 + 85`
- **Hack**: image token count is encoded in the proxy URL (`{file_id}_{tokens}.jpg`)

---

## 6. Plugin/Function System

### 6.1 Base Class `OpenAIFunction`

File: `functions/base.py`

```python
class OpenAIFunction(ABC):
    PARAMS_SCHEMA = OpenAIFunctionParams  # Pydantic model

    def __init__(self, user, db, context_manager, side_effects: SideEffectHandler, tool_call_id=None)

    async def run(self, params) -> Optional[str]        # abstract
    async def run_dict_args(self, params: dict)          # parsing from dict
    async def run_str_args(self, params: str)             # parsing from JSON string

    @classmethod def get_description(cls) -> str          # abstract
    @classmethod def get_name(cls) -> str                  # = cls.__name__
    @classmethod def get_params_schema(cls) -> dict        # JSON schema from Pydantic
    @classmethod def get_system_prompt_addition(cls)       # additional instructions for system prompt
```

### 6.2 Built-in Functions

| Class | File | Activation Condition | Description |
|-------|------|---------------------|-------------|
| `GenerateImageDalle3` | `functions/dalle_3.py` | `user.image_generation` AND role check | DALL-E 3 image generation (1024×1024, 1024×1792, 1792×1024). Adds system prompt for tailored prompts. Returns `None` (adds result to context itself) |
| `QueryWolframAlpha` | `functions/wolframalpha.py` | `ENABLE_WOLFRAMALPHA` | Queries to WolframAlpha API. Extracts Input interpretation, Result, Results fields |
| `TodoistAddTask` | `functions/todoist.py` | Admin-only (`USER_ROLE_MANAGER_CHAT_ID`) | Task creation in Todoist with optional date and duration |
| `CreateObsidianNote` | `functions/obsidian_echo.py` | Admin-only | Note creation via Obsidian Echo API (Bearer auth) |
| `VectorSearch` | `functions/vectara_search.py` | `VECTARA_RAG_ENABLED` AND documents in context | RAG search via Vectara. Input: query + document_ids. Returns top 5 results |
| `SaveUserSettings` | `functions/save_user_settings.py` | `user.system_prompt_settings_enabled` | Saving user settings to `system_prompt_settings` |

### 6.3 MCP Client

File: `functions/mcp/mcp_function_storage.py`

**`MCPFunctionManager`** — orchestrator:
- Connects to MCP server via `streamablehttp_client` (HTTP-based MCP)
- Calls `session.list_tools()` for dynamic tool discovery
- Creates `MCPFunction` instances for each discovered tool

**`MCPFunction`** — tool wrapper:
- Inherits from `OpenAIFunction`, but overrides `__call__` for lazy context binding
- Each `run()` opens a new HTTP connection to the MCP server
- Custom headers for authentication (Bearer token, etc.)

**Configuration** via `MCPServerConfig`:
```python
@dataclass
class MCPServerConfig:
    url: str                                    # MCP server URL
    min_role: UserRole                          # minimum role for access
    headers: Optional[dict[str, str]] = None    # custom HTTP headers
```

### 6.4 Function Registration Flow

`FunctionManager.process_functions()`:

1. **Static functions** — enabled unconditionally (WolframAlpha when `ENABLE_WOLFRAMALPHA`)
2. **Conditional functions** — depend on user settings and roles:
   - Todoist, Obsidian Echo → admin-only
   - DALL-E 3 → `user.image_generation` + role check
   - SaveUserSettings → `user.system_prompt_settings_enabled`
   - VectorSearch → `VECTARA_RAG_ENABLED` + documents in context
3. **MCP functions** — for each server from `settings.MCP_SERVERS`:
   - Check `check_access_conditions(mcp_config.min_role, user.role)`
   - HTTP request to server, `list_tools()`
   - On error — logging, skip (does not block other functions)
4. All functions are registered in `FunctionStorage` → provides `get_functions_info()` for LLM API

---

## 7. Access Control System

### 7.1 Role Hierarchy

```
STRANGER < BASIC < ADVANCED < ADMIN < NOONE
                                       │
                                 (impossible role,
                                  used for
                                  deprecated models)
```

Check: `check_access_conditions(required_role, user_role)` — comparison of indices in `ROLE_ORDER`.

### 7.2 Feature Access Matrix

| Feature | Setting in settings.py | Default Min Role |
|---------|----------------------|-----------------|
| Bot access | `USER_ROLE_BOT_ACCESS` | `BASIC` |
| Model selection | `USER_ROLE_CHOOSE_MODEL` | `BASIC` |
| Streaming responses | `USER_ROLE_STREAMING_ANSWERS` | `BASIC` |
| Image generation | `USER_ROLE_IMAGE_GENERATION` | `BASIC` |
| Text-to-Speech | `USER_ROLE_TTS` | `BASIC` |
| RAG (document upload) | `USER_ROLE_RAG` | `BASIC` |
| /usage_all (all users stats) | hardcoded | `ADMIN` |
| Full model list | hardcoded | `ADMIN` |
| Todoist integration | `USER_ROLE_MANAGER_CHAT_ID` | Admin (by telegram_id) |
| Obsidian Echo | `USER_ROLE_MANAGER_CHAT_ID` | Admin (by telegram_id) |
| Each MCP server | `MCPServerConfig.min_role` | Configurable per-server |

### 7.3 Role Management

When `ENABLE_USER_ROLE_MANAGER_CHAT = True`:
1. New user → notification to `USER_ROLE_MANAGER_CHAT_ID` with inline keyboard
2. Admin selects a role → update in DB
3. Bot commands are updated for the user according to their role

Implemented in `UserRoleManager` (`bot/user_role_manager.py`).

---

## 8. User Interface

### 8.1 Bot Commands

| Command | Description | Min Role |
|---------|-----------|---------|
| `/reset` | Reset current conversation | BOT_ACCESS |
| `/settings` | Open settings menu | BOT_ACCESS |
| `/models` | Open model selection menu | CHOOSE_MODEL |
| `/usage` | Monthly usage statistics | BOT_ACCESS |
| `/usage_all [-N]` | All users statistics (N — month offset) | ADMIN |
| `/text2speech` | Voice the last or replied message | TTS |

### 8.2 Settings Menu

Opened via `/settings`. Three types of settings:

**VisibleOptionsSetting** — all options visible inline:
- `current_model` — quick switch between GPT-4o / GPT-4-Turbo / GPT-3.5

**OnOffSetting** — on/off toggle:
- `use_functions` — use function calling
- `image_generation` — image generation
- `system_prompt_settings_enabled` — save user settings
- `voice_as_prompt` — voice as prompt (vs context)
- `function_call_verbose` — show function call details
- `streaming_answers` — streaming responses

**ChoiceSetting** — cyclic selection from a list:
- `all_models` — full model list (ADMIN only)
- `gpt_mode` — GPT mode (assistant / coach / ai dungeon)
- `tts-voice` — TTS voice (alloy / echo / fable / onyx / nova / shimmer)

Each setting has a `minimum_required_role` — hidden if the user's role is insufficient.

### 8.3 Models Menu

Opened via `/models`:
- Displays the current model with information: name, prices, capabilities, context config
- Inline keyboard with all available models (filtered by user role)
- Model switch → update `user.current_model` in DB

### 8.4 Content Type Handling

| Type | Processing |
|------|-----------|
| **Text** | Added to context as `DialogMessage(role="user")` |
| **Photo** | Largest resolution → image proxy URL with token count → sent to vision-capable model |
| **Voice/Audio** | Download → convert to MP3 (pydub) → Whisper STT → text to context. `voice_as_prompt` setting determines whether this is a prompt |
| **Document** | Extension check → upload to Vectara corpus → metadata to context as `MessageType.DOCUMENT` (25MB limit) |
| **Forwarded** | Added with attribution `@username:\n{text}`. `forward_as_prompt` setting determines whether this is a prompt |
| **Caption** | Processed as text (`message.caption → message.text`) |

---

## 9. Infrastructure and Deployment

### 9.1 Docker Compose Services

| Service | Image | Purpose | Ports | Depends On |
|---------|-------|---------|-------|-----------|
| `app` | chatgpt-tg (custom) | Main bot process (polling) | — | postgres |
| `update_keyboards` | chatgpt-tg | One-shot: update bot commands | — | app |
| `image_proxy` | chatgpt-tg | FastAPI proxy for Telegram File API → Vision | 8321 | app |
| `postgres` | postgres:15.3 | Database | 5432 | — |
| `pgweb` | sosedoff/pgweb | Web UI for DB | 8081 | postgres |

### 9.2 Image Proxy

File: `main_image_proxy.py` (FastAPI + uvicorn)

**Purpose:** OpenAI Vision API cannot directly access the Telegram File API. The image proxy downloads the file from Telegram and streams it to the caller.

**URL format:** `/{file_id}_{tokens}.jpg`
- `file_id` — Telegram file ID
- `tokens` — number of image tokens (hack for context counting)

**Configuration:** `IMAGE_PROXY_URL` must be set to a publicly accessible URL.

### 9.3 Configuration

File: `settings.py`

**Required:**
| Parameter | Description |
|-----------|-----------|
| `OPENAI_TOKEN` | OpenAI API key |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `IMAGE_PROXY_URL` | Public URL of image proxy |

**Providers (optional):**
| Parameter | Description |
|-----------|-----------|
| `ANTHROPIC_TOKEN` | Anthropic API key → enables Claude models |
| `OPENROUTER_TOKEN` | OpenRouter API key → enables WizardLM and others |

**Access control:**
| Parameter | Default | Description |
|-----------|---------|-----------|
| `USER_ROLE_DEFAULT` | `BASIC` | Default role for new users |
| `USER_ROLE_BOT_ACCESS` | `BASIC` | Minimum role for bot access |
| `USER_ROLE_CHOOSE_MODEL` | `BASIC` | Minimum role for model selection |
| `USER_ROLE_STREAMING_ANSWERS` | `BASIC` | Minimum role for streaming |
| `USER_ROLE_IMAGE_GENERATION` | `BASIC` | Minimum role for image gen |
| `USER_ROLE_TTS` | `BASIC` | Minimum role for TTS |
| `USER_ROLE_RAG` | `BASIC` | Minimum role for RAG |

**Integrations (optional):**
| Parameter | Description |
|-----------|-----------|
| `ENABLE_WOLFRAMALPHA` / `WOLFRAMALPHA_APPID` | WolframAlpha |
| `ENABLE_TODOIST_ADMIN_INTEGRATION` / `TODOIST_TOKEN` | Todoist (admin) |
| `ENABLE_OBSIDIAN_ECHO_ADMIN_INTEGRATION` / `OBSIDIAN_ECHO_*` | Obsidian Echo (admin) |
| `VECTARA_RAG_ENABLED` / `VECTARA_*` | Vectara RAG (experimental) |
| `MCP_SERVERS` | List of MCP servers |

**Tuning:**
| Parameter | Default | Description |
|-----------|---------|-----------|
| `OPENAI_CHAT_COMPLETION_TEMPERATURE` | 0.3 | Temperature for completions |
| `MESSAGE_EXPIRATION_WINDOW` | 3600 sec | Conversation lifetime window |
| `SUCCESSIVE_FUNCTION_CALLS_LIMIT` | 12 | Recursive function calls limit |

**Local overrides pattern:** at the end of `settings.py`, variables are overridden for local development. For production, environment variables should be used.

---

## 10. Data Models

### 10.1 Core Pydantic Models (`openai_helpers/chatgpt.py`)

```python
class DialogMessage:
    role: Optional[str]                              # "user", "assistant", "function", "tool", "system"
    name: Optional[str]                              # function name (for role="function")
    content: Union[str, List[DialogMessageContentPart], None]
    function_call: Optional[FunctionCall]             # legacy function calling
    tool_calls: Optional[List[ToolCall]]              # new tool calling
    tool_call_id: Optional[str]                       # ID for tool response

class FunctionCall:
    name: Optional[str]
    arguments: Optional[str]                          # JSON string

class ToolCall:
    id: str
    type: str                                         # "function"
    function: FunctionCall

class CompletionUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    model: str

class DialogMessageContentPart:
    type: str                                         # "text" | "image_url"
    text: Optional[str]
    image_url: Optional[DialogMessageImageUrl]
```

### 10.2 DB Models (`storage/db.py`)

```python
class User:
    id: int
    telegram_id: int
    current_model: str
    gpt_mode: str
    forward_as_prompt: bool
    voice_as_prompt: bool
    use_functions: bool
    auto_summarize: bool
    full_name: Optional[str]
    username: Optional[str]
    role: Optional[UserRole]
    streaming_answers: bool
    function_call_verbose: bool
    image_generation: bool
    tts_voice: str
    system_prompt_settings: Optional[str]
    system_prompt_settings_enabled: Optional[bool]

class Message:
    id: int
    user_id: int
    message: DialogMessage
    cdate: datetime
    activation_dtime: datetime
    previous_message_ids: List[int]
    tg_chat_id: int
    tg_message_id: int
    message_type: MessageType                         # MESSAGE | SUMMARY | RESET | DOCUMENT
```

### 10.3 Configuration Models (`llm_models.py`, `settings.py`)

```python
class LLModel:
    model_name: str
    api_key: str
    context_configuration: LLMContextConfiguration
    model_readable_name: str
    model_price: LLMPrice
    base_url: Optional[str]
    capabilities: LLMCapabilities
    minimum_user_role: UserRole
    api_client: Type[BaseLLMClient]

class LLMContextConfiguration:
    short_term_memory_tokens: int                     # threshold for summarization
    summary_length: int                               # max summary length
    hard_max_context_size: int                        # hard limit

class LLMCapabilities:
    function_calling: bool
    tool_calling: bool
    image_processing: bool
    streaming_responses: bool

class LLMPrice:
    input_tokens_price: Decimal                       # per 1000 tokens
    output_tokens_price: Decimal

@dataclass
class MCPServerConfig:
    url: str
    min_role: UserRole
    headers: Optional[dict[str, str]]
```

---

## 11. Project Structure

```
chatgpt-tg/
│
├── main.py                        # Entry point: Bot initialization, Dispatcher, polling
├── main_image_proxy.py            # Entry point: FastAPI image proxy service
├── settings.py                    # Central configuration (all settings)
├── requirements.txt               # Python dependencies (~30 packages)
├── Dockerfile                     # Python 3.11 + ffmpeg
├── docker-compose.yml             # 5 services: app, postgres, image_proxy, pgweb, update_keyboards
│
├── app/
│   ├── bot/
│   │   ├── telegram_bot.py        # TelegramBot class: handler registration, startup/shutdown
│   │   ├── message_processor.py   # Thin adapter: builds session, wires runtime + adapter
│   │   ├── telegram_runtime_adapter.py  # Consumes RuntimeEvents for Telegram streaming UI
│   │   ├── telegram_side_effects.py     # TelegramSideEffectHandler for function side effects
│   │   ├── batched_input_handler.py  # BatchedInputHandler: 300ms batching, builds UserInput
│   │   ├── chatgpt_manager.py     # ChatGptManager: wrapper over ChatGPT/Anthropic for usage tracking
│   │   ├── models_menu.py         # ModelsMenu: inline keyboard for model selection
│   │   ├── settings_menu.py       # Settings: inline keyboard for user settings
│   │   ├── user_role_manager.py   # UserRoleManager: role management via admin chat
│   │   ├── user_middleware.py     # UserMiddleware: user creation/update, access control
│   │   ├── scheduled_tasks.py     # Monthly usage reporting task
│   │   ├── cancellation_manager.py  # CancellationManager: streaming cancellation tokens
│   │   └── utils.py              # Utilities: send/edit message, TypingWorker, Timer, etc.
│   │
│   ├── runtime/
│   │   ├── runtime.py             # LLMRuntime protocol
│   │   ├── default_runtime.py     # DefaultLLMRuntime: current LLM logic (streaming, tool calls)
│   │   ├── conversation_session.py # ConversationSession dataclass
│   │   ├── user_input.py          # UserInput, TextInput, ImageInput, DocumentInput, VoiceTranscription
│   │   ├── events.py              # RuntimeEvent hierarchy (deltas, final, function events)
│   │   ├── side_effects.py        # SideEffectHandler protocol
│   │   └── context_utils.py       # add_user_input_to_context() shared utility
│   │
│   ├── context/
│   │   ├── context_manager.py     # ContextManager: orchestration of dialog + functions + system prompt
│   │   ├── dialog_manager.py      # DialogManager: history, summarization, message chains
│   │   └── function_manager.py    # FunctionManager: aggregation of static + conditional + MCP functions
│   │
│   ├── openai_helpers/
│   │   ├── chatgpt.py            # ChatGPT class: OpenAI API wrapper, streaming, Pydantic models
│   │   ├── anthropic_chatgpt.py  # AnthropicChatGPT: Anthropic API adapter
│   │   ├── llm_client.py         # BaseLLMClient, Generic/OpenAISpecific/Anthropic clients
│   │   ├── llm_client_factory.py # LLMClientFactory: client caching per model
│   │   ├── function_storage.py   # FunctionStorage: function registry, JSON schema extraction
│   │   ├── count_tokens.py       # Token counting: tiktoken, image tokens, fallbacks
│   │   ├── utils.py              # Usage price calculation, OpenAIAsync singleton
│   │   ├── whisper.py            # Whisper STT (gpt-4o-transcribe)
│   │   └── embeddings.py         # Embedding vectors (for RAG)
│   │
│   ├── functions/
│   │   ├── base.py               # OpenAIFunction ABC: base class for all tool functions
│   │   ├── dalle_3.py            # GenerateImageDalle3: DALL-E 3 image generation
│   │   ├── wolframalpha.py       # QueryWolframAlpha: WolframAlpha queries
│   │   ├── todoist.py            # TodoistAddTask: Todoist task creation
│   │   ├── obsidian_echo.py      # CreateObsidianNote: Obsidian note creation
│   │   ├── vectara_search.py     # VectorSearch: RAG search via Vectara
│   │   ├── save_user_settings.py # SaveUserSettings: saving user preferences
│   │   └── mcp/
│   │       └── mcp_function_storage.py  # MCPFunctionManager + MCPFunction: MCP client
│   │
│   ├── storage/
│   │   ├── db.py                 # DB class: all SQL queries, User/Message Pydantic models
│   │   ├── user_role.py          # UserRole enum, ROLE_ORDER, check_access_conditions()
│   │   └── vectara.py            # VectaraCorpusClient: document upload/search
│   │
│   └── llm_models.py            # LLModel class, get_models() registry, LLMPrice/Capabilities/Context
│
├── migrations/
│   ├── sql/                      # 13 SQL migration files (0000–0012)
│   ├── pg_init.sh                # PostgreSQL initialization script
│   ├── entrypoint.sh             # Docker entrypoint for migrations
│   └── wait-for-it.sh            # PostgreSQL readiness wait utility
│
├── tests/                        # E2E tests (real DB, mocked LLM/Telegram)
│   ├── conftest.py               # Core fixtures: db, bot, dispatcher, cleanup
│   ├── helpers/
│   │   ├── telegram_factory.py   # Factory for fake aiogram Update objects
│   │   ├── mock_llm_client.py    # MockLLMClient with canned responses
│   │   └── bot_spy.py            # Assertion helpers over Bot.request calls
│   └── e2e/
│       ├── test_simple_message.py  # Text message → LLM response (4 tests)
│       ├── test_commands.py        # /reset, /usage (2 tests)
│       └── test_sub_dialogue.py    # Multi-message dialogue context (1 test)
│
├── scripts/
│   ├── test.sh                   # Run tests locally (postgres in docker, pytest on host)
│   ├── test_docker.sh            # Run tests fully in docker
│   ├── update_keyboards.py       # Update bot commands for all users
│   ├── send_management_menus.py  # Send role management menus
│   └── create_vectara_corpus.py  # Initialize Vectara RAG corpus
│
├── specs/                        # Project specifications
│
├── README.md
└── LICENSE
```

---

## 12. Known Limitations and Technical Debt

| Area | Description |
|------|-------------|
| **Anthropic tokenizer** | GPT-4 tokenizer is used as an approximation — inaccurate token counting for Claude |
| **Image token hack** | Image token count is encoded in the proxy URL (`file_id_1000.jpg`) instead of metadata in `DialogMessage` |
| **MCP connections** | Each MCP tool call opens a new HTTP connection (TODO: `ClientSessionGroup` for reuse) |
| **Anthropic summarization** | Summarization for Anthropic models falls back to GPT-4o (cross-provider dependency) |
| **CancellationManager** | Memory leak: if a message is not cancelled, the token is not deleted |
| **TTS model** | Hardcoded `tts-1` (TODO: selection via user settings) |
| **Migrations** | Forward-only, no rollback mechanism |
| **Voice handling** | Saves to a temporary file on disk (TODO: streaming) |
| **Unknown models** | Tokenizer fallback to `len(str)` — rough estimate |
| **Anthropic client** | Anthropic model detection via `model_name == ANTHROPIC_CLAUDE_35_SONNET` (TODO: factory) |
| **Settings file** | Local overrides with hardcoded credentials at end of file — needs migration to .env |