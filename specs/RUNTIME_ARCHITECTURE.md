# LLM Runtime Architecture

> Transport-agnostic LLM execution layer, decoupled from Telegram

---

## 1. Overview

The LLM Runtime layer (`app/runtime/`) separates LLM logic (context management, streaming, tool calling, usage tracking) from transport logic (Telegram message editing, thinking emoji, cancel buttons, 4080-char splits).

This enables:
1. Plugging different runtimes into the same Telegram bot (Anthropic Agents SDK, OpenAI Responses API, custom pipelines)
2. Plugging the same runtime into a different transport (HTTP API, CLI, WebSocket)

### Dependency Direction

```
Telegram Transport (app/bot/)
        │
        ▼
   LLM Runtime (app/runtime/)
        │
        ▼
   Context Layer (app/context/)
   LLM Clients  (app/openai_helpers/)
   Functions     (app/functions/)
   Storage       (app/storage/)
```

The runtime layer has **zero** aiogram imports. Functions have **zero** aiogram imports — they use `SideEffectHandler` protocol for transport interactions.

---

## 2. Runtime Package Structure

```
app/runtime/
├── __init__.py                 # Public exports
├── runtime.py                  # LLMRuntime protocol
├── default_runtime.py          # DefaultLLMRuntime — current implementation
├── agent_runtime.py            # AgentRuntime — multi-turn agent with plans and background tasks
├── plan_manager.py             # PlanManager — plan state machine and DB persistence
├── background_task_manager.py  # BackgroundTaskManager — sub-agent lifecycle
├── conversation_session.py     # ConversationSession dataclass
├── user_input.py               # UserInput, TextInput, ImageInput, DocumentInput, VoiceTranscription
├── events.py                   # RuntimeEvent hierarchy
├── side_effects.py             # SideEffectHandler protocol
└── context_utils.py            # add_user_input_to_context() — shared utility
```

---

## 3. Core Types

### 3.1 LLMRuntime Protocol

```python
class LLMRuntime(Protocol):
    async def process_turn(
        self,
        user_input: UserInput,
        session: ConversationSession,
        is_cancelled: Callable[[], bool],
    ) -> AsyncGenerator[RuntimeEvent, None]: ...
```

The protocol is minimal: it takes user input, session info, and a cancellation check. It yields events. Everything else (context management, LLM client selection, tool execution) is an implementation detail.

Note: `context_manager` is NOT in the protocol. `DefaultLLMRuntime` accepts it in the constructor, but alternative runtimes may manage context entirely differently.

### 3.2 UserInput

```python
@dataclass
class UserInput:
    text_inputs: List[TextInput]              # text and/or image messages
    documents: List[DocumentInput]            # uploaded documents (metadata only)
    voice_transcriptions: List[VoiceTranscription]  # transcribed voice messages
```

A `UserInput` represents a preprocessed batch of user messages. The transport layer handles I/O-heavy preprocessing (file downloads, Whisper transcription, Vectara upload) and packs the results into `UserInput`. The runtime adds these items to context and calls the LLM.

Each item carries a `tg_message_id` for sub-dialogue chain tracking. This is a transport-layer identifier used by the database for message threading — it's not Telegram-specific in concept, but the value comes from Telegram.

**TextInput**: a single text message, optionally with images.
- `images: List[ImageInput]` stores `file_id` + dimensions. The proxy URL is constructed by the runtime (in `context_utils.py`), not by the transport.

**DocumentInput**: metadata only (`document_id`, `document_name`). The actual file upload to Vectara happens in the transport layer.

**VoiceTranscription**: transcribed text. The `tg_message_id` is the ID of the bot's reply containing the transcription text (so that context chains include the transcription message).

### 3.3 ConversationSession

```python
@dataclass
class ConversationSession:
    chat_id: int
    reply_to_message_id: Optional[int] = None
    is_forwarded: bool = False
```

Transport-agnostic conversation identification. Used by `DialogManager` to load the correct message chain from the database.

### 3.4 RuntimeEvent Hierarchy

```python
RuntimeEvent                        # base
├── StreamingContentDelta           # partial LLM output (visible_text, thinking_text, is_thinking)
├── FinalResponse                   # complete response (dialog_message, needs_context_save)
├── FunctionCallStarted             # function execution begins (name, args, tool_call_id)
├── FunctionCallCompleted           # function execution ends (name, args, result, tool_call_id)
└── ErrorEvent                      # error occurred (error, message)
```

**StreamingContentDelta**: emitted for each streaming chunk. Contains both `visible_text` (for display) and `thinking_text` (from `<think>` tags). The `is_thinking` flag tells the adapter whether to show thinking UI.

**FinalResponse**: emitted after the streaming generator is fully consumed. Contains the complete `DialogMessage` with thinking stripped. The `needs_context_save` flag tells the adapter whether it needs to save content to context (True when there's content; False for function-call-only responses where the runtime saves it internally).

**FunctionCallStarted/Completed**: emitted for each function call. The adapter uses these for verbose display. The runtime uses `FunctionCallCompleted.result` internally to decide whether to pass the result back to the LLM.

### 3.5 SideEffectHandler Protocol

```python
class SideEffectHandler(Protocol):
    async def send_message(self, text: str) -> int: ...
    async def send_photo(self, photo_bytes: bytes, caption: Optional[str] = None) -> int: ...
```

Functions use this instead of `aiogram.types.Message` to send messages/photos. Returns the transport message ID. The Telegram implementation is `TelegramSideEffectHandler` (`app/bot/telegram_side_effects.py`).

---

## 4. Request Flow

```
User sends messages to Telegram
            │
            ▼
┌────────────────────────────────────────────────┐
│  BatchedInputHandler                            │
│  • Collects messages into batch (300ms)         │
│  • Transport preprocessing:                     │
│    voice → Whisper → VoiceTranscription          │
│    document → Vectara upload → DocumentInput     │
│    photo → ImageInput (file_id + dimensions)     │
│    text → TextInput                              │
│  • Builds UserInput from preprocessed data      │
│  • batch_is_prompt()?                           │
│    No  → MessageProcessor.add_context_only()    │
│    Yes → MessageProcessor.process()             │
└─────────────────────┬──────────────────────────┘
                      ▼
┌────────────────────────────────────────────────┐
│  MessageProcessor (thin adapter)                │
│  • Builds ConversationSession from aiogram msg  │
│  • Creates ContextManager                       │
│  • Creates DefaultLLMRuntime(db, user,          │
│      side_effects, context_manager)             │
│  • Creates TelegramRuntimeAdapter(message,      │
│      user, context_manager)                     │
│  • Calls adapter.handle_turn(runtime,           │
│      user_input, session, is_cancelled)         │
└─────────────────────┬──────────────────────────┘
                      ▼
┌────────────────────────────────────────────────┐
│  TelegramRuntimeAdapter                         │
│  • Iterates runtime.process_turn() events       │
│  • StreamingContentDelta → throttled message    │
│    editing, thinking emoji, cancel button        │
│  • FinalResponse → split + send/edit final msg, │
│    save to context with real TG message_id       │
│  • FunctionCallCompleted → verbose display      │
└─────────────────────┬──────────────────────────┘
                      ▼
┌────────────────────────────────────────────────┐
│  DefaultLLMRuntime.process_turn()               │
│  1. Add UserInput items to context              │
│  2. Select LLM client (ChatGPT/Anthropic)       │
│  3. Build system prompt + function storage      │
│  4. Stream LLM response → yield deltas          │
│  5. yield FinalResponse                         │
│  6. If tool calls → execute → yield events      │
│     → pass results to LLM → recurse to step 4  │
└────────────────────────────────────────────────┘
```

---

## 5. Context Saving: Split Responsibility

This is the most important architectural subtlety to understand.

**User messages** → saved by the runtime (via `add_user_input_to_context()`), before calling the LLM.

**Assistant content messages** → saved by the adapter, because only the adapter knows the transport message ID (needed for sub-dialogue branching via `tg_message_id`).

**Function-call-only assistant messages** (no visible content) → saved by the runtime with `tg_message_id=-1`.

**Function/tool responses** → saved by the runtime with `tg_message_id=-1`.

The `FinalResponse.needs_context_save` flag coordinates this: when True, the adapter saves the content messages; when False (function-call-only response), the runtime has already saved it.

This split exists because the runtime is transport-agnostic and cannot know the Telegram message ID, while the database requires it for message chain tracking. An alternative runtime that doesn't use our database would simply ignore this concern.

---

## 6. Files Changed from Original Architecture

### Transport layer (`app/bot/`) — Telegram-specific

| File | Role |
|------|------|
| `telegram_runtime_adapter.py` | Consumes RuntimeEvents, manages Telegram streaming UI |
| `telegram_side_effects.py` | Implements SideEffectHandler for Telegram |
| `message_processor.py` | Thin adapter: builds session, wires runtime + adapter |
| `batched_input_handler.py` | Transport preprocessing, builds UserInput |

### Runtime layer (`app/runtime/`) — transport-agnostic

| File | Role |
|------|------|
| `runtime.py` | LLMRuntime protocol |
| `default_runtime.py` | Current implementation using ChatGPT/Anthropic clients |
| `conversation_session.py` | Session identification |
| `user_input.py` | Input data types |
| `events.py` | Event hierarchy |
| `side_effects.py` | SideEffectHandler protocol |
| `context_utils.py` | Shared utility for adding UserInput to context |

### Context layer (`app/context/`) — no aiogram imports

| File | Change |
|------|--------|
| `context_manager.py` | Accepts `ConversationSession` instead of `aiogram.types.Message` |
| `dialog_manager.py` | Accepts `ConversationSession` instead of `aiogram.types.Message` |

### Functions (`app/functions/`) — no aiogram imports

| File | Change |
|------|--------|
| `base.py` | Accepts `SideEffectHandler` instead of `aiogram.types.Message` |
| `dalle_3.py` | Uses `self.side_effects.send_photo()` |
| `save_user_settings.py` | Uses `self.side_effects.send_message()` |
| `vectara_search.py` | Uses `self.side_effects.send_message()` |
| `mcp/mcp_function_storage.py` | `__call__` accepts `side_effects` instead of `message` |

---

## 6a. AgentRuntime

`AgentRuntime` (`app/runtime/agent_runtime.py`) is an alternative to `DefaultLLMRuntime` for agent mode — a multi-turn LLM loop with plan management and background sub-agents.

### Key differences from DefaultLLMRuntime

| Aspect | DefaultLLMRuntime | AgentRuntime |
|--------|------------------|--------------|
| Tool call loop | Up to `SUCCESSIVE_FUNCTION_CALLS_LIMIT` (12) | Up to `AGENT_MAX_ITERATIONS` (30) |
| Plan management | No | `PlanManager` with DB persistence, periodic reminders |
| Background tasks | No | `SpawnTask` creates sub-agents with own tool access |
| MCP servers | `MCP_SERVERS` | `MCP_SERVERS` + `MCP_SERVERS_AGENT` |
| System prompt | gpt_mode + tool additions | `AGENT_SYSTEM_PROMPT` + gpt_mode + tool additions |
| Context saving | Split between runtime and adapter | Same split pattern |

### Agent Loop

1. Load tools: MCP tools + agent tools (plan, task, schedule)
2. Build system prompt with `AGENT_SYSTEM_PROMPT` prefix
3. Loop (up to `AGENT_MAX_ITERATIONS`):
   - Inject plan reminder if due (every `AGENT_PLAN_REMINDER_INTERVAL` iterations)
   - Inject completed background task results
   - Call LLM → yield `StreamingContentDelta` events
   - If tool calls: execute, yield `FunctionCall` events, continue loop
   - If content: yield `FinalResponse`, break

### Plan Reminders

Plan state is injected as context messages (not system prompt) to preserve prompt caching:
- Iteration 0: always inject if plan exists
- Every N iterations: re-inject if no plan tool was called
- Format: `<plan-reminder>...</plan-reminder>` as user message + assistant acknowledgment

### Background Sub-Agents

`SpawnTask` creates a sub-agent that runs in a separate coroutine with its own LLM call loop (up to `AGENT_SUB_AGENT_MAX_ITERATIONS`). Results are delivered via `<background-results>` messages when the main agent's next iteration begins.

---

## 7. Adding a New Runtime

To add an alternative runtime (e.g., Anthropic Agents SDK). For a real-world example, see `AgentRuntime` (`app/runtime/agent_runtime.py`) which implements a multi-turn agent loop with plan management and background sub-agents while following the same `LLMRuntime` protocol.

### Step 1: Implement the protocol

```python
# app/runtime/agents_sdk_runtime.py

class AgentsSDKRuntime:
    def __init__(self, db: DB, user: User, side_effects: SideEffectHandler):
        self.db = db
        self.user = user
        self.side_effects = side_effects

    async def process_turn(self, user_input, session, is_cancelled):
        # Your own context management, tool loop, etc.
        agent = Agent(model="claude-sonnet-4-20250514", tools=[...])
        result = Runner.run_streamed(agent, self._build_prompt(user_input))

        async for event in result.stream_events():
            yield StreamingContentDelta(
                visible_text=event.text,
                thinking_text='',
                is_thinking=False,
            )

        yield FinalResponse(
            dialog_message=self._build_dialog_message(result),
            needs_context_save=True,
        )
```

### Step 2: Wire it up

In `MessageProcessor.process()` (or a new factory):

```python
async def process(self, is_cancelled, user_input: UserInput):
    session = self._build_session()
    context_manager = await build_context_manager(self.db, self.user, session)
    side_effects = TelegramSideEffectHandler(self.message)

    # Choose runtime based on user settings, model, or config
    if self.user.current_model.startswith('claude'):
        runtime = AgentsSDKRuntime(self.db, self.user, side_effects)
    else:
        runtime = DefaultLLMRuntime(self.db, self.user, side_effects, context_manager)

    adapter = TelegramRuntimeAdapter(self.message, self.user, context_manager)
    await adapter.handle_turn(runtime, user_input, session, is_cancelled)
```

### Step 3: Handle context saving

If your runtime manages its own context (e.g., Agents SDK has its own memory), set `needs_context_save=False` on `FinalResponse` and handle persistence internally. The adapter will skip saving.

If your runtime uses the shared `ContextManager`, pass it via constructor (like `DefaultLLMRuntime` does) and follow the same save pattern.

### Key constraints for any runtime:
- Must yield `StreamingContentDelta` events for streaming display
- Must yield `FinalResponse` at the end of each LLM response (even after tool calls that trigger another LLM round)
- Must yield `FunctionCallStarted`/`FunctionCallCompleted` for tool calls (adapter uses these for verbose display)
- Can manage context however it wants internally
- Can use `SideEffectHandler` for function side effects (sending photos, messages)

---

## 8. Adding a New Transport

To add a non-Telegram transport (e.g., HTTP API):

### Step 1: Implement a transport adapter

```python
# app/api/http_runtime_adapter.py

class HTTPRuntimeAdapter:
    def __init__(self, context_manager: ContextManager):
        self.context_manager = context_manager

    async def handle_turn(self, runtime, user_input, session, is_cancelled):
        chunks = []
        async for event in runtime.process_turn(user_input, session, is_cancelled):
            if isinstance(event, StreamingContentDelta):
                yield {"type": "delta", "text": event.visible_text}
            elif isinstance(event, FinalResponse):
                if event.needs_context_save and event.dialog_message.content:
                    # Save with tg_message_id=-1 (no Telegram message)
                    await self.context_manager.add_message(event.dialog_message, -1)
                yield {"type": "final", "content": event.dialog_message.content}
```

### Step 2: Implement SideEffectHandler

```python
class HTTPSideEffectHandler:
    async def send_message(self, text: str) -> int:
        # Return a placeholder message_id (or store in your system)
        return -1

    async def send_photo(self, photo_bytes: bytes, caption=None) -> int:
        # Encode and return via HTTP response
        return -1
```

### Key constraint:
- Sub-dialogue branching (`reply_to_message_id` in `ConversationSession`) may not apply to non-chat transports. Set it to `None` — `DialogManager` will load the latest conversation instead of a specific branch.

---

## 9. Known Trade-offs

| Trade-off | Description | Impact |
|-----------|-------------|--------|
| **Split context saving** | Content messages saved by adapter (needs transport message_id), function messages saved by runtime | Two code paths mutate the same `ContextManager`; alternative runtimes must understand the `needs_context_save` contract |
| **`tg_message_id` in UserInput** | Transport-layer IDs leak into runtime types | Needed for database message chaining; non-Telegram transports use `-1` |
| **Image proxy URL in runtime** | `context_utils.py` constructs proxy URLs from `settings.IMAGE_PROXY_URL` | The image proxy is infrastructure, not Telegram-specific; acceptable for now |
| **Anthropic client detection** | `model_name == ANTHROPIC_CLAUDE_35_SONNET` hack in DefaultLLMRuntime | Should be refactored to factory pattern (pre-existing tech debt) |
