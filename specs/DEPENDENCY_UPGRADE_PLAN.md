# Dependency Upgrade Plan: aiogram 3 + current LLM SDKs

Status: **in progress** — Phases 0–3 done (requirements restructured, aiogram 3.30, pydantic v2 API, openai 3.3.1; 136 tests green). This document is the executable checklist for the migration. Each phase ends with a green `bash scripts/test.sh` and its own commit; phases are done strictly in order on one branch.

## 1. Why this document exists

The project runs on `aiogram==2.25.1` (end-of-life, aiohttp 3.8) and a flat `pip freeze` in `requirements.txt` that pins ~70 transitive packages (`certifi==2023.5.7`, `aiohttp==3.8.4`, `aiofiles==23.1.0`, `magic-filter==1.0.9`, …). Bumping aiogram alone against that file always ends in `ResolutionImpossible`, because the *transitive* pins — not the SDKs — conflict with aiogram 3.

Research findings that shape the plan (verified against the real packages in a scratch venv with `pip install --dry-run --report`, imports and `inspect`):

| Finding | Consequence |
|---|---|
| `pydantic` is already **2.11** in the project. Only v1-style call sites remain (`.dict()`, `.copy()`, `.parse_raw()`, `.schema()`). | No pydantic migration; a small cleanup phase. |
| `aiogram 3.30.0` needs `aiohttp>=3.9,<3.15`, `aiofiles>=23.2.1`, `magic-filter>=1.0.12`, `pydantic>=2.4.1,<2.14`, Python ≥3.10. | Resolves fine with the **current** `openai 1.35.8 / anthropic 0.29.0 / mcp 1.13.0` once transitive pins are dropped. |
| The new SDK majors — `openai 3.x`, `anthropic 1.0`, `mcp 2.x` — moved from `httpx` to **`httpx2`** (pydantic's maintained fork). `langfuse 4.x` stays on `httpx<1`. | Both packages coexist (different distribution names). Our own HTTP code keeps plain `httpx`. Verified: `langfuse.openai.AsyncOpenAI` constructs on top of openai 3; `AnthropicInstrumentor().instrument()` works with anthropic 1.0. |
| An intermediate rung exists that is still on `httpx<1`: `openai 2.54.0`, `anthropic 0.125.0`, `mcp 1.29.1`. | Fallback if a phase 3–5 target proves problematic. |
| `sandbox/` is a separate service with its own `sandbox/server/requirements.txt` and no aiogram/pydantic coupling. | Out of scope. |

Python stays **3.11** (`Dockerfile`, `.python-version`); every target supports it.

## 2. Target versions

| Package | Now | Target | Notes |
|---|---|---|---|
| aiogram | 2.25.1 | **3.30.0** | Phase 1 |
| aiohttp / aiofiles / magic-filter | 3.8.4 / 23.1.0 / 1.0.9 | unpinned (transitive) | resolved by aiogram |
| pydantic | 2.11.7 | 2.13.x | Phase 2 |
| openai | 1.35.8 | **3.3.x** | Phase 3, httpx2 |
| anthropic | 0.29.0 | **1.0.x** | Phase 4, httpx2 |
| mcp | 1.13.0 | **2.1.x** | Phase 5, httpx2, new `Client` API |
| httpx | 0.27.1 | 0.28.x | stays a **direct** dependency (tavily, sandbox client, wolfram, dalle, image proxy) |
| langfuse | >=3 (4.14 installed) | 4.14.x, pinned | wrapper verified against openai 3 |
| opentelemetry-instrumentation-anthropic | unpinned (0.62.3) | 0.62.x, pinned | `_instruments = anthropic>=0.3.11`, verified with 1.0 |
| pytest / pytest-asyncio | 8.3.4 / 0.24.0 | 9.x / 1.4.x | Phase 6 (removes custom `event_loop` fixture) |
| numpy, fastapi, uvicorn, starlette, asyncpg, tiktoken | old pins | latest | Phase 6 |

## 3. Principles

- One branch, sequential phases, one commit per phase, tests green at every commit.
- `requirements.txt` lists **direct dependencies only**, pinned with `==`. Transitive packages are resolved by pip at build time. (`pip freeze` output may be kept in a separate `requirements.lock` later if reproducibility becomes a problem; not part of this plan.)
- Never combine an aiogram change and an SDK change in one commit — the test suite is the only safety net and its failures must be attributable.
- Every phase: `bash scripts/test.sh`, then `docker-compose up -d --build` and the manual smoke list from §11.

## 4. Phase 0 — restructure `requirements.txt` (no version changes) — ✅ done

Goal: prove the flat-freeze was the only blocker, with zero behaviour change.

Rewrite `requirements.txt` to direct dependencies at their *current* versions:

```
aiogram==2.25.1
openai==1.35.8
anthropic==0.29.0
mcp==1.13.0
httpx==0.27.1
asyncpg==0.27.0
tiktoken==0.7.0
pydub==0.25.1
fastapi==0.116.1
uvicorn==0.35.0
python-multipart==0.0.20
langfuse==4.14.4
opentelemetry-instrumentation-anthropic==0.62.3
croniter==1.4.1
dateparser==1.2.0
numpy==1.25.2
requests==2.32.5
pytz==2023.3
python-dateutil==2.8.2
pytest==8.3.4
pytest-asyncio==0.24.0
```

`async-lru` is imported (`app/`) and must stay. Drop packages that nothing imports: `pydantic-settings`, `python-dotenv`, `huggingface-hub`, `tokenizers`, `hf-xet`, `jsonschema`, `docstring-parser`, `fsspec`, `filelock`, `tqdm`, `Babel`, `PyYAML`. (`pydantic` itself arrives via openai/anthropic/aiogram 3; add it explicitly in Phase 2 when we pin it.)

Verify: fresh venv install, `pip check`, `bash scripts/test.sh`, `docker-compose build`.

Result: fresh py3.11 venv installs cleanly, `pip check` clean, 136 tests pass. Transitive drift observed: aiohttp 3.8.4→3.8.6, pydantic 2.11.7→2.13.4, magic-filter 1.0.9→1.0.12.

## 5. Phase 1 — aiogram 2.25 → 3.30 — ✅ done

Bump `aiogram==3.30.0`. Everything below is in `app/bot/` (14 files), two entrypoints, two scripts, and `tests/`. `app/runtime/`, `app/context/`, `app/functions/`, `app/openai_helpers/`, `app/storage/` are aiogram-free and untouched.

Official guide: https://docs.aiogram.dev/en/latest/migration_2_to_3.html

### 1a. Entry point, Dispatcher, routing

- `main.py`: `Dispatcher(bot)` → `Dispatcher()`; keep `Bot(token=...)` (no global `parse_mode` — we pass it per call today; optionally `DefaultBotProperties(parse_mode=...)` later).
- `app/bot/telegram_bot.py`:
  - `from aiogram.utils import executor` / `executor.start_polling(...)` → `asyncio.run(dp.start_polling(bot))`; `on_startup`/`on_shutdown` → `dp.startup.register(...)` / `dp.shutdown.register(...)` (signature no longer receives the dispatcher positionally — accept `**kwargs`).
  - `register_message_handler(h, commands=[...])` → `dp.message.register(h, Command('settings'))` (or a `Router`).
  - `register_message_handler(handle, content_types=[TEXT, VIDEO, PHOTO, VOICE, DOCUMENT, AUDIO, VIDEO_NOTE])` → `F.content_type.in_({ContentType.TEXT, ...})` with `from aiogram.enums import ContentType`.
  - `register_callback_query_handler(h, lambda c: c.data == 'hide')` → `dp.callback_query.register(h, F.data == 'hide')`; same pattern in `settings_menu.py:118`, `models_menu.py:17`, `user_role_manager.py:18-23`, `cancellation_manager.py:27` (`F.data.startswith(PREFIX)` / `F.data.contains(PREFIX)`).
  - `message.get_args()` (`/usage_all` handler, line 142) → add `command: CommandObject` parameter and use `command.args`.
  - `bot.answer_callback_query(cq.id)` → `cq.answer()` (also in settings/models/role/cancellation managers).
- `scripts/update_keyboards.py`, `scripts/send_management_menus.py`: `Dispatcher(bot)` → `Dispatcher()`; `await bot.get_session(); session.close()` → `await bot.session.close()`.
- `main_image_proxy.py:18`: `bot.get_file_url(path)` removed → build `f'{bot.session.api.file_url(bot.token, file_path)}'` (or the literal `https://api.telegram.org/file/bot{token}/{file_path}`).

### 1b. Middleware (`app/bot/user_middleware.py`) — full rewrite

```python
from aiogram import BaseMiddleware
class UserMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Message, data):
        ...  # same user lookup/creation logic
        if not user_have_access:
            await event.answer(...)
            return  # replaces raise CancelHandler()
        data['user'] = user
        return await handler(event, data)
```

Register as `dp.message.middleware(UserMiddleware(db))` (inner middleware, message events only — same scope as `on_pre_process_message`). Handlers keep their `user: User` kwarg — v3 injects `data` keys as kwargs.

### 1c. Exceptions

`aiogram.utils.exceptions.BadRequest` → `aiogram.exceptions.TelegramBadRequest` in `telegram_bot.py`, `batched_input_handler.py`, `service_message.py`, `telegram_runtime_adapter.py`, `utils.py`, `tests/e2e/test_video_note.py`.
`CantParseEntities` has no v3 class: in `app/bot/utils.py:135-155` (`send_telegram_message` / `edit_telegram_message`) catch `TelegramBadRequest` and retry without `parse_mode` when `"can't parse entities" in e.message` (keep the generic retry as today). Note `TelegramBadRequest(method=..., message=...)` requires both args — matters for the test in `test_video_note.py:220`.

### 1d. Enums

`types.ParseMode` / `from aiogram.types import ParseMode` → `from aiogram.enums import ParseMode` (`telegram_runtime_adapter.py:4`, `telegram_bot.py:132`, `user_role_manager.py:58,63`, `settings_menu.py:121`, `models_menu.py:20,92`). `'typing'` string in `send_chat_action` → `ChatAction.TYPING` (`telegram_runtime_adapter.py:72`) — and switch that call to kwargs.

### 1e. Keyboards

`InlineKeyboardMarkup()` + `.add(button)` (11 call sites in `utils.py`, `user_role_manager.py`, `settings_menu.py`, `models_menu.py`, `telegram_runtime_adapter.py`) → `InlineKeyboardBuilder()`; `.add(...)`/`.row(...)`/`.adjust(1)`; `.as_markup()`. `InlineKeyboardButton(text=..., callback_data=...)` kwargs form already used — fine.

### 1f. Files

- `types.InputFile(io.BytesIO(bytes), filename=...)` (`utils.py:167`) → `BufferedInputFile(bytes, filename=...)`.
- Raw `bytes` to `answer_photo` / `send_photo` (`utils.py:157-163`, `bot_side_effects.py:22`) → `BufferedInputFile(photo_bytes, filename='image.png')`.
- `message.answer_voice(open(path, 'rb'))` (`telegram_bot.py:203`) → `FSInputFile(path)`.
- `bot.download_file(file.file_path, destination=path)` (`batched_input_handler.py:185,274`) — signature is `download_file(file_path, destination=None, ...)`, still works; keep, but tests mock it (see 1h).

### 1g. Frozen models and keyword-only calls

- `message.text = message.caption` (`batched_input_handler.py:310`) → aiogram 3 models are frozen pydantic models; use a local `text = message.caption or message.text` and thread it through (check every downstream read of `.text` in that branch).
- Positional `bot.edit_message_text(text, chat_id, message_id)` / `delete_message(chat_id, id)` / `send_message(chat_id, text)` → keyword args (`utils.py:151,154`, `telegram_side_effects.py:26`, `bot_side_effects.py:18,22,26`, `service_message.py:159`, `scheduled_tasks.py:70`). `BotCommandScopeChat(user.telegram_id)` → `BotCommandScopeChat(chat_id=...)` (`user_role_manager.py:98`).
- Verified **not** breaking in 3.30: `message.forward_from` / `forward_sender_name` / `forward_from_chat` (deprecated, still present — a later cleanup can move to `forward_origin`), `Chat.full_name`, `BotCommand(command='/reset')`, `message.bot`.

### 1h. Test infrastructure (`tests/`) — the biggest single chunk

- `tests/conftest.py`:
  - Replace the `Bot.request` `AsyncMock` with a **mocked session**: subclass `aiogram.client.session.base.BaseSession`, implement `make_request(self, bot, method, timeout=None)`. `method.__api_method__` gives `'sendMessage'` etc., `method.model_dump(exclude_none=True)` gives the data dict, and the existing `_make_bot_request_handler` logic produces the result dict. Return a parsed model via `self.check_response(bot, method, status_code=200, content=json.dumps({'ok': True, 'result': result})).result` so the returned `Message` is bound to the bot. Record `(api_method, data, result)` into `bot.sent_responses` and keep a `call_args_list`-like list for `BotSpy`.
  - `Bot(token=..., session=MockedSession())`; `Dispatcher()`; `await telegram_bot.on_startup()`; **remove** the `TelegramObject.bot` property patch (v3 binds bot via `feed_update`).
  - Keep the `Timer` patch and DB/LLM cache handling unchanged.
- `tests/helpers/bot_spy.py`: read from the mocked session's recorded calls instead of `mock_bot.request.call_args_list`; method names stay camelCase.
- `tests/helpers/telegram_factory.py`: `types.Update(**d)` → `Update.model_validate(d)`; v3 validation is stricter — the dicts already carry `file_unique_id`, `chat_instance`, `date`, so expect few fixes. Keep `forward_*` legacy fields (still accepted).
- `dp.process_update(update)` → `dp.feed_update(bot, update)` — 134 call sites in 15 e2e files (mechanical sed; `bot_app` fixture already yields `mock_bot`).
- `tests/e2e/test_error_handling.py`: `feed_update` re-raises unhandled handler exceptions (verified in `ErrorsMiddleware`), so `pytest.raises` keeps working.
- `tests/e2e/test_video_note.py`, `test_bash_sandbox.py`: `mock_bot.download_file = AsyncMock(...)` still valid (method exists); `types.File(...)` kwargs unchanged; `BadRequest('blocked')` → `TelegramBadRequest(method=SendMessage(chat_id=0, text=''), message='blocked')`; `patch.object(types.Message, 'reply', ...)` still works (patching a class attribute, not an instance).

### 1i. Docs

Update `CLAUDE.md` (Libraries: aiogram 3.x; mocking description), `specs/PROJECT_SPEC.md:15`, `specs/E2E_TESTS.md:22,41` (mocked session + `feed_update` instead of `Bot.request` + ContextVar patch).

Verify: tests; live smoke (§11). Commit: `Migrate to aiogram 3`.

Result / deviations from the plan above:
- `message.reply()` in v3 sends `reply_parameters: {message_id}` instead of `reply_to_message_id`; one spy assertion updated.
- Tests that call `process_batch` directly must mount messages with `message.as_(mock_bot)` (no dispatcher → no bot binding).
- Forwards: code now reads `forward_origin` (`MessageOriginUser/HiddenUser/Chat/Channel`) with legacy-field fallback; the test factory emits `forward_origin` (Bot API 7 no longer sends `forward_from*`).
- `BotCommand` entries lost their leading `/` (v3 accepts both, the slash-less form is canonical).
- `MockedSession.stream_content` is a stub; `bot.download_file` is mocked per test as before.
- Live Telegram smoke (§11) still to be run by hand.

## 6. Phase 2 — pydantic v1-style cleanup — ✅ done

Pin `pydantic==2.13.x` explicitly. Replace deprecated calls (all emit `PydanticDeprecatedSince20` today):

| File | Change |
|---|---|
| `app/functions/base.py:40` | `PARAMS_SCHEMA.parse_raw(params)` → `model_validate_json(params)` |
| `app/functions/base.py:56` | `PARAMS_SCHEMA.schema()` → `model_json_schema()` |
| `app/openai_helpers/chatgpt.py:63,198` | `.copy(update=...)` → `.model_copy(update=...)` |
| `app/openai_helpers/chatgpt.py:79`, `anthropic_chatgpt.py:207` | `.dict(exclude_none=True)` → `.model_dump(exclude_none=True)` |
| `app/openai_helpers/chatgpt.py:215` | `DialogMessage(**message.dict())` on an openai model → `model_dump()` |
| `app/openai_helpers/anthropic_chatgpt.py:145,151,153` | `.dict()` on anthropic models → `model_dump()` |
| `app/bot/telegram_runtime_adapter.py:189` | `.copy(update=...)` → `.model_copy(update=...)` |
| `app/openai_helpers/chatgpt.py:213,242,246,252` | `dict(resp.usage)` / `dict(delta)` rely on BaseModel iteration — switch to `model_dump()` |
| `tests/helpers/mock_llm_client.py:14-19,123,147-168` | drop the `.dict` / `__iter__` / `keys` shims, expose `model_dump()` instead |

Optional tidy-ups surfaced by the audit (do only if cheap): `Optional[...]` fields without defaults in `db.py:15` `User` and `chatgpt.py:16` `FunctionCall` (required-but-nullable in v2), `AnthropicContentPart.content: List[...] = None`.

Commit: `Replace pydantic v1-style calls`.

Result: all sites above converted; `pydantic==2.13.4` pinned; the optional field tidy-ups were skipped (no behaviour change needed). `delta.model_dump()` excludes `function_call`/`tool_calls` because `dict(delta)` used to leave nested models as objects that `merge_dicts` skipped — those deltas are accumulated separately. Suite passes with `-W error::DeprecationWarning:pydantic`.

## 7. Phase 3 — openai 1.35 → 3.x — ✅ done

Bump `openai==3.3.x`; add nothing for httpx2 (it is a transitive dep; we do not pass httpx objects to the SDK). Keep `httpx==0.28.x` as a direct dependency for our own clients.

Checks / changes:
- `app/openai_helpers/llm_client.py`, `utils.py`: `AsyncOpenAI(api_key=, base_url=)` — unchanged API.
- `app/openai_helpers/chatgpt.py:367` `resp_generator.response.aclose()` — `AsyncStream.response` still exists in 3.3; keep, but wrap in `contextlib.suppress(Exception)`.
- `app/bot/telegram_bot.py:201` `response.astream_to_file(...)` — still present on `HttpxBinaryResponseContent` in 3.3 (legacy); keep.
- `app/openai_helpers/embeddings.py:27` `response['data']` is pre-1.x dead code → `response.data`.
- `langfuse.openai` drop-in: verified to construct against openai 3; confirm a trace actually appears in Langfuse during smoke (the wrapper patches `openai.resources.chat.completions`, which still exists).
- TLS: httpx2 uses the OS trust store, not certifi. `python:3.11` (Debian) ships `ca-certificates`; verify `/etc/ssl/certs` exists in the image. If a custom `OPENAI_BASE_URL` uses a private CA, set `SSL_CERT_FILE`.
- OpenRouter / LM Studio go through the same client — smoke one non-OpenAI model.

Fallback: `openai==2.54.0` (still httpx).

Commit: `Upgrade openai SDK to 3.x`.

Result: `openai==3.3.1` installs alongside `httpx==0.27.1` (langfuse) and `httpx2==2.12.0`; no application code changes were needed — `AsyncStream.response`/`aclose()` (already wrapped in `suppress`) and `astream_to_file` still exist, `embeddings.py` was fixed in Phase 2, `langfuse.openai.AsyncOpenAI` constructs on 3.3.1. Live check of Whisper/TTS/DALL-E and a Langfuse trace remains part of the manual smoke.

## 8. Phase 4 — anthropic 0.29 → 1.0

Bump `anthropic==1.0.x`. Verified: `anthropic.AsyncClient` alias exists; we don't use `temperature`/`top_p`/`top_k`, `completions`, `with_raw_response`, custom httpx objects, or Bedrock — so no mandatory code changes.

- `app/openai_helpers/anthropic_chatgpt.py:171`: replace `raise NotImplementedError` for unknown stream events with `logger.debug` + `continue` (newer SDKs emit more event types).
- `app/openai_helpers/llm_client.py:81` `max_tokens=4096` hardcoded — unrelated but worth a settings knob while here (optional).
- `main.py` `AnthropicInstrumentor().instrument()` — verified importing and instrumenting against 1.0; check a Langfuse trace in smoke.

Fallback: `anthropic==0.125.0`.

Commit: `Upgrade anthropic SDK to 1.0`.

## 9. Phase 5 — mcp 1.13 → 2.x

Bump `mcp==2.1.x`. Only `app/functions/mcp/mcp_function_storage.py` changes. Migration guide: https://py.sdk.modelcontextprotocol.io/migration/

- `streamablehttp_client(url, headers=) + ClientSession(read, write) + initialize()` → `async with Client(transport, read_timeout_seconds=settings.MCP_TOOL_CALL_TIMEOUT) as client:`. Headers: `Client` accepts a transport or URL string; for custom headers build the transport from `mcp.client.streamable_http.streamable_http_client(url, http_client=httpx2.AsyncClient(headers=...))` — confirm the exact wiring against the installed 2.1 source at implementation time (the `StreamableHTTPTransport(url)` constructor itself takes no headers).
- `tool.inputSchema` → `tool.input_schema`; `tools.nextCursor` → `next_cursor`.
- `call_tool(name, arguments=..., read_timeout_seconds=timedelta(...))` → `call_tool(name, arguments)` (timeout is a float on the client / per call); errors raise `MCPError` instead of `result.isError` — catch and return the message as the tool result string.
- `mcp.types` still works as an alias of `mcp_types`.
- If `httpx2` is imported directly here, add `httpx2>=2.5` to `requirements.txt`.

Tests: MCP is disabled in tests (`settings.MCP_SERVERS = []`); smoke against a real MCP server from `settings_local`.

Fallback: `mcp==1.29.1`.

Commit: `Upgrade mcp SDK to 2.x`.

## 10. Phase 6 — remaining dependencies

- `pytest==9.x`, `pytest-asyncio==1.4.x`: pytest-asyncio 1.0 removed support for a user-defined `event_loop` fixture. Delete the `event_loop` fixture in `tests/conftest.py:97`; rely on `asyncio_default_fixture_loop_scope = session` in `pytest.ini` (already set) and add `asyncio_default_test_loop_scope = session` so session-scoped async fixtures (`db_pool`) share the loop with tests.
- `numpy` 2.x (`embeddings.py` only), `asyncpg` 0.31, `tiktoken` 0.14, `fastapi`/`uvicorn`/`starlette` latest (`main_image_proxy.py` only), `croniter`, `dateparser`, `requests`, `pytz`, `python-dateutil` latest.
- After this phase run `pip check` and keep the freeze output in the PR description for reference.

Commit: `Upgrade test and utility dependencies`.

## 11. Verification checklist (every phase)

1. `bash scripts/test.sh` — all e2e + unit tests green.
2. `docker-compose up -d --build` (with `python main.py` entrypoint) — app starts, no import errors in `docker-compose logs -f app`.
3. Manual Telegram smoke:
   - plain text → streamed reply, cancel button works, final message rendered with Markdown fallback;
   - voice message → transcription alias + reply; video note;
   - `/settings`, `/models` menus (callback queries), `/reset`, `/usage`, `/usage_all -1` (admin, command args), `/text2speech` (voice file upload);
   - image generation (DALL-E → `send_photo` with bytes);
   - agent mode: document upload to sandbox, `send_file_to_chat` (document upload), a bash tool call;
   - reply to a bot message → sub-dialogue branch;
   - image proxy URL resolves (`main_image_proxy.py`) — vision prompt with a photo;
   - Anthropic model streaming + tool call; OpenRouter model; MCP tool if configured;
   - Langfuse trace visible (if enabled).

## 12. Known "silent" hazards

These do not raise at import time and will only show up at runtime or in tests:

- frozen aiogram models (`message.text = ...`);
- `message.get_args()` returning nothing (method gone → `AttributeError` only when `/usage_all` is invoked);
- Markdown parse-error fallback relying on `CantParseEntities`;
- httpx2 OS trust store in minimal images / behind corporate proxies;
- `NotImplementedError` on new Anthropic stream event types;
- pytest-asyncio 1.x ignoring the custom `event_loop` fixture (tests would run on different loops than the session-scoped pool → `asyncpg` "attached to a different loop" errors).
