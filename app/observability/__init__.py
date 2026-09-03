"""Observability facade for LLM tracing (Langfuse).

Rule: `langfuse` / `opentelemetry` may only be imported inside this package
(`_langfuse.py`). Every other file interacts with tracing through this facade
only, in 1-2 lines per call site, so the whole integration can be removed by
deleting this package and those call sites. See specs/OBSERVABILITY.md.

The backend is chosen once, lazily, on the first facade call from
settings.LANGFUSE_ENABLED: the real Langfuse backend or a no-op backend with
the same duck-typed API. The lazy choice keeps this module importable from
inside the settings import itself (settings_local may pull in app.llm_models,
which imports this package before settings has finished initializing).
"""
import settings

_backend = None


def _get_backend():
    global _backend
    if _backend is None:
        if settings.LANGFUSE_ENABLED:
            from app.observability import _langfuse
            _backend = _langfuse
        else:
            from app.observability import _noop
            _backend = _noop
    return _backend


def init() -> None:
    """Initialize tracing. Call once at process start, before any LLM client is created."""
    _get_backend().init()


def shutdown() -> None:
    """Flush and close the tracing client. Call on process shutdown."""
    _get_backend().shutdown()


def create_openai_client(api_key, base_url=None):
    """Return an AsyncOpenAI instance — Langfuse drop-in wrapper when enabled, plain otherwise."""
    return _get_backend().create_openai_client(api_key, base_url)


def begin_turn(*, name: str, user_id: str, session_id, input_text=None, tags=()):
    """Open the root observation for one conversation turn.

    Returns a TurnHandle: set_output(text) remembers the final answer,
    end(error=None) closes the turn (idempotent, never raises). Must be called
    and ended within the same asyncio task.
    """
    return _get_backend().begin_turn(
        name=name, user_id=user_id, session_id=session_id,
        input_text=input_text, tags=tags,
    )


def tool_span(name: str, input=None):
    """Context manager for a tool execution span. Yields a SpanHandle with set_output(value)."""
    return _get_backend().span(name=name, as_type='tool', input=input)


def agent_span(name: str, input=None):
    """Context manager for a sub-agent run span. Yields a SpanHandle with set_output(value)."""
    return _get_backend().span(name=name, as_type='agent', input=input)


def turn_session_id(session, context_manager):
    """Langfuse session id for a turn: '{chat_id}:{root db message id}' of the dialog branch.

    Reply branches share the conversation-tree root, so they land in the same session.
    Must be called after user input is added to context (a fresh dialog has no
    root row before that). Returns None when the dialog has no messages.
    """
    messages = context_manager.dialog_manager.messages
    if not messages:
        return None
    return f'{session.chat_id}:{messages[0].id}'


def turn_input_text(user_input) -> str:
    """Text-only representation of UserInput for the trace input (never image payloads)."""
    parts = [t.text for t in user_input.text_inputs if t.text]
    parts += [vt.text for vt in user_input.voice_transcriptions]
    parts += [
        f'[file: {sf.filename}]' + (f' {sf.caption}' if sf.caption else '')
        for sf in user_input.sandbox_files
    ]
    return '\n'.join(parts)
