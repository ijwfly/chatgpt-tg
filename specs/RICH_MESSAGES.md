# Rich Messages Migration (Telegram Bot API 10.1–10.3)

Status: **Phases 0–3 done** — rich answers, draft streaming in private chats with a rich service-message fallback. Phases 4–6 pending. Each phase ends with a green `bash scripts/test.sh` and its own commit; phases run in order on branch `claude/rich-messages` (based on `claude/dependency-upgrade-plan`, PR targets that branch).

## 1. Why

Telegram added **Rich Messages** in Bot API 10.1 (June 2026) and extended them in 10.2/10.3 (July/August 2026):

- `sendRichMessage(chat_id, rich_message=InputRichMessage(markdown=...))` — GitHub-flavoured Markdown (headings, tables, nested lists, code fences with language, LaTeX, `<details>`, footnotes), up to **32768** characters (vs 4096 for `sendMessage`).
- `editMessageText(..., rich_message=InputRichMessage)` — editing rich messages in place.
- `sendRichMessageDraft(chat_id, draft_id, rich_message)` — an ephemeral streaming preview: animated, supports `<tg-thinking>…</tg-thinking>`, **private chats only**, disappears after 30 s or when the bot sends a message; the final content **must** be sent with `sendRichMessage`.
- Bot API 10.3 (24 Aug 2026): `can_stop` / `keep_on_stop` on drafts → native Stop button; the bot receives an Update with `stopped_message_generation: {chat, draft_id, message_thread_id?}`.

Today the bot passes LLM output verbatim with legacy `parse_mode=Markdown` and retries in plain text on `can't parse entities`; streaming edits a real message every second with an inline Stop button; long answers are split at 4080 characters without regard to markup (a split inside a code fence silently degrades the whole message to plain text).

## 2. Verified facts (aiogram 3.30.0 installed = Bot API 10.2; docs at core.telegram.org/bots/api, 10.3)

- Available in aiogram 3.30: `Bot.send_rich_message(chat_id, rich_message, reply_parameters=, reply_markup=, …) -> Message`, `Bot.send_rich_message_draft(chat_id, draft_id: int != 0, rich_message, message_thread_id) -> bool`, `Bot.edit_message_text(…, rich_message=)`, `InputRichMessage(markdown= | html= | blocks=, media=, skip_entity_detection=, is_rtl=)`, `Message.rich_message: RichMessage(blocks)` (a rich message has `text=None`), `ContentType.RICH_MESSAGE`. `Message` has no `answer_rich_message`/`reply_rich_message` helpers — replies use `reply_parameters=ReplyParameters(message_id=…)`.
- Missing in aiogram 3.30 (Bot API 10.3): `can_stop`, `keep_on_stop`, `MessageGenerationStopped`, `Update.stopped_message_generation`. aiogram PR #1888 is open, no release. Workarounds that work with 3.30:
  - `TelegramMethod` has `extra="allow"` → `SendRichMessageDraft(…, can_stop=True)` serialises the extra field.
  - `TelegramObject` has `extra="allow"` → an unknown update field is available as `update.model_extra['stopped_message_generation']`.
  - `Dispatcher._listen_update` raises `SkipHandler` (+ `RuntimeWarning`) for unknown update types, but **outer middleware on `dp.update` runs before it** — the shim lives there.
  - `Dispatcher.start_polling` computes `allowed_updates` from registered handlers, so `stopped_message_generation` must be added to `allowed_updates` explicitly or Telegram will never deliver it.
- Rich Markdown syntax: `**bold**`, `*italic*`, `~~strike~~`, `` `code` ``, `==mark==`, `||spoiler||`, `# H1`…`###### H6`, ``` fences with language, `---`, `-`/`1.` lists and `- [ ]` task lists, `>` quotes, GFM tables, `[^1]` footnotes, `$…$` / `$$…$$` LaTeX, `<details>`, `<u>`, `<sub>`, `<sup>`. Markdown "can contain arbitrary HTML" — unescaped `<` in prose may be interpreted as a tag. `<tg-thinking>` is allowed only in drafts. Limits: 32768 characters, 500 blocks, 16 nesting levels, 20 table columns.
- Telegram's exact error text for malformed rich markdown is not documented — checked during manual testing (§7).

## 3. Decisions

| Question | Decision |
|---|---|
| Streaming | Drafts (`sendRichMessageDraft`) in private chats; real message + `editMessageText(rich_message=)` in groups or when a draft call fails mid-turn. |
| Stop button while drafting | Native `can_stop=True` + a middleware shim for the `stopped_message_generation` update until aiogram ships 10.3. The inline Stop button stays on the edit path. |
| Rollout | No feature flags. The legacy `parse_mode=Markdown` path is removed; rollback is `git revert`. |
| Scope | LLM answers (streamed and final, scheduled-task results) + `/usage`, `/models`, admin user cards. Plain service texts (errors, upload confirmations, transcriptions, verbose tool output, plan messages, captions) stay plain. |
| Storage | Unchanged: the assistant message is stored as its markdown source, as before. No migrations. |

## 4. Design

### 4.1 `app/bot/rich_messages.py` — transport helpers

- `RICH_MESSAGE_LENGTH_CUTOFF = 30000` (headroom below 32768).
- `send_rich_message(message, markdown, reply_markup=None) -> Message` — reply vs answer with the same rule as `utils.send_telegram_message` (reply only when the user's message is itself a reply). Falls back to a plain `sendMessage` (no parse_mode) when Telegram rejects the markup (`is_parse_error`).
- `send_rich_message_to_chat(bot, chat_id, markdown) -> Message` — for `BotSideEffectHandler` / scheduler / admin chat.
- `edit_rich_message(message, markdown, message_id, reply_markup=None)` — `edit_message_text(rich_message=…)` with the same plain-text fallback.
- `send_rich_draft(bot, chat_id, draft_id, markdown, can_stop=True) -> bool` — `bot(SendRichMessageDraft(…, can_stop=can_stop))` (extra field until aiogram 3.31).
- `split_markdown(content, max_len)` — the greedy splitter from `TelegramRuntimeAdapter._split_dialog_message` (`\n` → `.` → ` `), now code-fence aware: a part that ends inside a ``` block gets the fence closed, and the next part re-opens it with the same language.
- `escape_rich_markdown(text)` — replaces `utils.escape_tg_markdown` for user-supplied names in admin cards: backslash-escapes markdown specials and turns `<`/`>` into `&lt;`/`&gt;`.
- `utils.is_parse_error` matches `"can't parse"` (covers both `can't parse entities` and rich-markup errors).

### 4.2 Streaming — `app/bot/service_message.py`, `app/bot/telegram_runtime_adapter.py`

Two live-output implementations behind one small interface (`set_content`, `set_thinking`, `set_hint`, `freeze`, `clear`, `finish`):

1. **`DraftStream`** — private chats. `draft_id = user_message_id * 100 + phase` (non-zero, unique per agent phase). Dedup + 1 s throttle as today. `set_thinking` → `<tg-thinking>🧠 last line</tg-thinking>` (same `_format_thinking_display` rules), `set_hint` → `<tg-thinking>Running X...</tg-thinking>`, `set_content` → accumulated markdown minus the trailing (partial) word, shown once ≥ `MIN_STREAMING_CONTENT_LEN`. A keepalive task re-sends the last draft every 20 s while the turn is running (drafts expire after 30 s, tool calls can take longer). Any `TelegramBadRequest` from a draft call logs and switches the turn to `ChatServiceMessage`.
2. **`ChatServiceMessage`** — groups and fallback: the existing class, sending/editing via `send_rich_message` / `edit_rich_message` with the inline Stop button. `parse_mode` parameter removed.

`handle_turn`: `DraftStream` when `message.chat.type == 'private'`, otherwise `ChatServiceMessage`. `FinalResponse`: `split_markdown(content, RICH_MESSAGE_LENGTH_CUTOFF)`; on the draft path every chunk is a fresh `send_rich_message` (the draft disappears by itself); on the edit path the first chunk is edited into the service message (no delete/create flicker), the rest are sent. `context_manager.add_message(dm, message_id)` unchanged. Overflow during streaming (> cutoff) keeps the `⏳...` + freeze behaviour. Verbose tool output stays plain.

### 4.3 Native stop — `app/bot/cancellation_manager.py`, `app/bot/telegram_bot.py`

- `StoppedGenerationMiddleware` (outer middleware on `dispatcher.update`): if `update.model_extra` has `stopped_message_generation`, cancel the token for `payload['chat']['id']` and return without calling the handler (so the dispatcher's unknown-update warning never fires). Registered by `CancellationManager`.
- `TelegramBot.run()`: `start_polling(bot, allowed_updates=[*dispatcher.resolve_used_update_types(), 'stopped_message_generation'])`.
- Follow-up (not in this branch): once aiogram releases Bot API 10.3 support, replace the shim with the native observer and pass `can_stop` as a named parameter.

### 4.4 Menus

- `/usage` — `**bold**` rows, sent with `send_rich_message` (+ Hide button).
- `/models` — `get_model_info` in rich markdown (bold labels, real `- ` lists); `send_rich_message` / `edit_rich_message` (the edit path gains the plain-text fallback it lacked).
- Admin user cards (`user_role_manager`) — `**bold**`, `escape_rich_markdown`, `send_rich_message_to_chat` / `edit_rich_message`.
- `settings_menu` — the no-op `parse_mode=MARKDOWN` is dropped.
- `scheduler_service` — the LLM result of a scheduled task goes through `send_rich_message_to_chat`.
- Removed: `ParseMode` imports in `app/bot/*`, `utils.escape_tg_markdown`, dead `utils.detect_and_extract_code` / `CodeFragment`.

### 4.5 Test infrastructure

- `tests/conftest.py::_fake_telegram_result`: `sendRichMessage` → a message dict **without `text`** (as Telegram returns), `sendRichMessageDraft` → `True`.
- `tests/helpers/bot_spy.py`: text of a request = `data['text']` or `data['rich_message']['markdown']`; `get_sent_messages()` covers `sendMessage` + `sendRichMessage`; new `get_rich_messages()`, `get_plain_messages()`, `get_drafts()`.
- `tests/helpers/telegram_factory.py`: `chat_type` parameter for group scenarios; `make_stopped_generation_update(chat_id, draft_id)`.

## 5. Phases

| # | Phase | Status |
|---|---|---|
| 0 | Branch `claude/rich-messages`, this spec | ✅ |
| 1 | `rich_messages.py` helpers, fence-aware splitter, `is_parse_error`, test infrastructure, splitter unit tests | ✅ |
| 2 | Final answers via `sendRichMessage` (adapter + scheduler), new cutoff | ✅ |
| 3 | Streaming via `DraftStream` with `ChatServiceMessage` fallback (rich edits) | ✅ |
| 4 | Native Stop: `can_stop`, `StoppedGenerationMiddleware`, `allowed_updates` | ⬜ |
| 5 | Menus (`/usage`, `/models`, admin cards, settings), cleanup | ⬜ |
| 6 | Docs (`CLAUDE.md`, `PROJECT_SPEC.md`, `RUNTIME_ARCHITECTURE.md`, `E2E_TESTS.md`, `CHANGELOG.md`), PR | ⬜ |

### Phase 1 result

`app/bot/rich_messages.py` (`send_rich_message`, `send_rich_message_to_chat`, `edit_rich_message`, `edit_rich_message_in_chat`, `send_rich_draft`, `split_markdown`, `escape_rich_markdown`), `is_parse_error` widened to `"can't parse"`, `sendRichMessage`/`sendRichMessageDraft` fakes in `tests/conftest.py`, `BotSpy` reads text from `rich_message.markdown` too (`get_rich_messages`, `get_plain_messages`, `get_drafts`, `get_all_draft_texts`), `make_text_message(chat_type=)` and `make_stopped_generation_update` in the factory, `tests/unit/test_rich_messages.py` for the splitter and escaper. Verified that `SendRichMessageDraft(..., can_stop=True)` serialises the extra field with aiogram 3.30.

### Phase 2 result

`ChatServiceMessage` sends/edits through `send_rich_message`/`edit_rich_message` (no `parse_mode` parameter any more), so both the streamed preview and the finalised answer are rich; `TelegramRuntimeAdapter` splits with `split_markdown` at `RICH_MESSAGE_LENGTH_CUTOFF` (verbose tool output keeps the 4080 plain cutoff), the remaining chunks go through `send_rich_message`. `BotSideEffectHandler.send_rich_message` + `scheduler_service` send the scheduled-task result as rich markdown. `MockedSession.fail_next(api_method, exc)` injects Telegram errors; `tests/e2e/test_rich_messages.py` covers the verbatim markdown payload + DB row, plain fallback on `can't parse`, and `reply_parameters` in sub-dialogues. The split test lowers the cutoff via monkeypatch.

### Phase 3 result

`DraftStream` (`service_message.py`) and `ChatServiceMessage` share one live-output interface (`set_thinking` / `set_hint` / `set_content` / `finish` / `freeze` / `clear` / `failed` / `needs_cleanup`); `TelegramRuntimeAdapter._new_live_output` picks `DraftStream` for `ChatType.PRIVATE` (`draft_id = user_message_id * 100 + phase`) and a `ChatServiceMessage` with the Stop keyboard otherwise; `show()` swaps in a service message as soon as a draft call fails. Drafts already carry `can_stop=True`. Thinking and tool hints are `<tg-thinking>` drafts, the finished answer is a fresh `sendRichMessage` without keyboard; the keepalive re-sends the last draft every 20 s. `BotSpy.get_all_shown_texts` / `assert_shown_text_contains` include drafts. Tests: private drafts (single draft id, one final rich message, no edit/delete), `<tg-thinking>` for thinking + hint, group chat edit path with Stop button, draft failure fallback, distinct draft ids per agent phase; the two legacy streaming tests now assert drafts.

## 6. Tests to add (`tests/e2e/test_rich_messages.py` unless noted)

- Final answer is sent as `sendRichMessage` with `rich_message.markdown == content`; plain fallback when the mocked session raises `can't parse …`.
- Splitter: parts ≤ cutoff, fence closed/reopened across a boundary, language preserved (unit test).
- Private chat streaming: drafts with the same `draft_id`, `can_stop=True`, thinking inside `<tg-thinking>`, exactly one final `sendRichMessage`, no `editMessageText` / `deleteMessage`.
- Group chat streaming: `sendRichMessage` + `editMessageText(rich_message=…)` with the Stop keyboard, final edit in place.
- Draft failure mid-turn → the turn continues on the edit path.
- Agent mode: two phases use two draft ids.
- `stopped_message_generation` update → token cancelled, partial answer finalised, no `RuntimeWarning`.
- `/usage`, `/models` (send + edit) render rich markdown (`test_commands.py`).

## 7. Risks / manual checks

- Exact Telegram error text for malformed rich markdown (send a broken `<details>`); widen `is_parse_error` if the wording differs.
- Rate limits on `sendRichMessageDraft` (throttle stays 1 s; raise on 429).
- `<tg-thinking>` inside `markdown=` (documented as allowed) and the 20 s keepalive during a long `bash_exec` in agent mode.
- Old Telegram clients: rendering of rich messages is Telegram's fallback, not controllable here.
- LLM prose with a bare `<` (`a<b`) outside code may be parsed as HTML; watch for it, add minimal escaping outside fences if needed.
- Manual smoke: answer with table/code/formula, answer > 30k characters, private-chat streaming (animation, native Stop), group streaming (edit + button), agent mode with tool calls, `/usage`, `/models`, new-user card in the admin chat, cancellation.
