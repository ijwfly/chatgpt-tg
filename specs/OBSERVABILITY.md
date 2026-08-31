# Observability (Langfuse)

LLM tracing via [Langfuse](https://langfuse.com). Designed to be **easily removable**: the
whole integration lives behind a facade, and every call site outside it is 1–2 lines.

## What is traced

One trace per conversation turn, with nested observations:

```
Trace (user_id = db user.id, session_id = "{chat_id}:{root db message id}")
└─ default-turn / agent-turn                ← root span, opened by the runtime
   ├─ generation                            ← auto: langfuse.openai wrapper (OpenAI-compatible)
   │                                          or OTel AnthropicInstrumentor (Anthropic)
   ├─ tool:web_search_agent                 ← tool execution span
   │   └─ web-agent                         ← isolated web agent sub-run
   │       ├─ generation …
   │       └─ tool:tavily_search            ← parallel tools are sibling spans
   ├─ tool:SpawnTask
   │   └─ sub-agent                         ← may outlive its parent span (background task)
   └─ generation                            ← follow-up call after tool results
```

- **Trace input**: user text (text inputs + voice transcriptions + file placeholders; never
  image payloads). **Trace output**: the final visible answer. Errors mark the root span
  `ERROR` with the exception message.
- Generations pick up `user_id` / `session_id` / `tags` automatically from
  `propagate_attributes` entered by the turn — there is no per-call metadata plumbing.

**Not traced** (same as before this integration): whisper, TTS, dalle-3, embeddings (they
use the raw `OpenAIAsync` singleton) and context summarization attribution.

## Semantics

- `session_id = "{chat_id}:{root db message id}"` — the dialog conversation tree.
  Reply branches share the tree root, so they land in the same Langfuse session.
  Context expiration / `/reset` starts a new tree → new session.
- `user_id` — internal db `user.id` (not the telegram id).
- Tags: `default` (DefaultLLMRuntime) / `agent` (AgentRuntime). Scheduled tasks run
  through AgentRuntime and are traced the same way.

## Configuration

| Env var | Meaning |
|---|---|
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | Enable tracing (both required) |
| `LANGFUSE_BASE_URL` | Langfuse host (default `https://cloud.langfuse.com`) |
| `LANGFUSE_ENVIRONMENT` | Optional environment tag (`production`, `staging`, …) |

The backend (real vs no-op) is chosen **once at import time** from
`settings.LANGFUSE_ENABLED` — changing it requires a process restart. Note that
`LANGFUSE_ENABLED` is computed before `settings_local.py` overrides are applied: if you set
the keys in `settings_local.py` instead of env vars, also set `LANGFUSE_ENABLED = True` there.

## Architecture and THE RULE

> `langfuse` / `opentelemetry` may only be imported inside `app/observability/`
> (specifically `_langfuse.py`). Everything else calls the facade.

```
app/observability/
├── __init__.py   # facade: init/shutdown, create_openai_client, begin_turn,
│                 # tool_span/agent_span, turn_session_id/turn_input_text helpers
├── _noop.py      # no-op backend (Langfuse disabled)
└── _langfuse.py  # real backend — the ONLY langfuse/opentelemetry imports
```

Call sites (each 1–2 lines):

| File | Call |
|---|---|
| `main.py` | `observability.init()` |
| `app/bot/telegram_bot.py` (`on_shutdown`) | `observability.shutdown()` (flush) |
| `app/openai_helpers/llm_client.py` | `observability.create_openai_client(...)` |
| `app/runtime/default_runtime.py` | `begin_turn`/`end`, `set_output`, `tool_span` |
| `app/runtime/agent_runtime.py` | `begin_turn`/`end`, `set_output`, `tool_span` ×2, `agent_span` (sub-agent) |
| `app/runtime/web_agent_runner.py` | `agent_span` (web agent), `tool_span` |

The turn is opened by the runtime inside `process_turn` right before the first LLM call
(after user input is added to context — only then does a fresh dialog have its root row for
the session id) and closed in `try/except/finally` (`end()` is idempotent). The runtime
generator and its consumer run in the same asyncio task, so OTel context enter/exit tokens
always match. Tool tasks spawned with `asyncio.gather` / `create_task` inherit a context
copy — their spans nest under whatever span was current at spawn time.

## Removal recipe

1. Delete `app/observability/` and `tests/unit/test_observability.py`.
2. Remove the call sites listed above (grep `observability`):
   - `main.py`: the import and `observability.init()`.
   - `app/bot/telegram_bot.py`: the import and `observability.shutdown()`.
   - `app/openai_helpers/llm_client.py`: restore `self.client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)` (+ `import openai`).
   - `app/runtime/default_runtime.py`, `app/runtime/agent_runtime.py`: drop `begin_turn`/`end`/`set_output`/`self._turn`, unwrap the `tool_span`/`agent_span` `with`-blocks.
   - `app/runtime/web_agent_runner.py`: drop the `run_web_agent` wrapper (rename `_run_web_agent_inner` back), unwrap `tool_span`.
3. Delete `langfuse==…` and `opentelemetry-instrumentation-anthropic==…` from `requirements.txt`.
4. Delete the `LANGFUSE_*` block from `settings.py`.
5. Delete this document.
6. Verify: `grep -rni "langfuse\|observability" app/ main.py settings.py` returns nothing.

## Gotchas

- **Streaming + cancellation**: when the user presses Stop, the response stream is
  force-closed; the Langfuse generation for that call may have missing/partial usage.
  The bot's own billing (`completion_usage` table) is independent and unaffected.
- **Sub-agent spans outliving `tool:SpawnTask`**: SpawnTask returns immediately while the
  background sub-agent keeps running — its span closes later than its parent. Valid in
  Langfuse, just looks unusual on the timeline.
- **No double-tracing of Anthropic**: `langfuse.openai` patches only OpenAI clients, the
  OTel instrumentor patches only the `anthropic` SDK; `instrument()` is guarded to run once.
- **httpx pin**: langfuse 4.x requires `httpx<1` — the repo pins `httpx==0.28.1` alongside
  `httpx2` for openai/anthropic. Don't bump langfuse without checking this arrangement.
- **Large payloads**: generations record full request messages; models with
  `image_input_format='base64'` put image data into the trace. Consider Langfuse masking
  if this becomes a problem.
- Tracing failures never break the bot: the real backend catches its own exceptions and
  degrades to no-op, `TurnHandle.end()` never raises.
