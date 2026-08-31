"""Langfuse observability backend. The ONLY module allowed to import langfuse/opentelemetry.

Tracing must never break the bot: every entry point catches its own exceptions
and degrades to the no-op behavior.
"""
import logging
import os
from contextlib import ExitStack, contextmanager

import settings
from app.observability._noop import NoopTurnHandle, NoopSpanHandle

logger = logging.getLogger(__name__)

_instrumented = False


def init():
    """Set Langfuse env vars from settings and instrument the Anthropic SDK. Idempotent."""
    global _instrumented

    os.environ.setdefault('LANGFUSE_PUBLIC_KEY', settings.LANGFUSE_PUBLIC_KEY)
    os.environ.setdefault('LANGFUSE_SECRET_KEY', settings.LANGFUSE_SECRET_KEY)
    os.environ.setdefault('LANGFUSE_HOST', settings.LANGFUSE_BASE_URL)
    if settings.LANGFUSE_ENVIRONMENT:
        os.environ.setdefault('LANGFUSE_TRACING_ENVIRONMENT', settings.LANGFUSE_ENVIRONMENT)

    if not _instrumented:
        try:
            from opentelemetry.instrumentation.anthropic import AnthropicInstrumentor
            AnthropicInstrumentor().instrument()
            _instrumented = True
            logger.info('Langfuse: Anthropic instrumentation enabled')
        except ImportError:
            logger.warning(
                'Langfuse: opentelemetry-instrumentation-anthropic not installed, '
                'Anthropic calls won\'t be traced'
            )

    logger.info(f'Langfuse observability enabled (base_url={settings.LANGFUSE_BASE_URL})')


def shutdown():
    """Flush buffered spans and close the client. Blocking is fine at process shutdown."""
    try:
        from langfuse import get_client
        get_client().shutdown()
    except Exception as e:
        logger.warning(f'Langfuse shutdown failed: {e}')


def create_openai_client(api_key, base_url=None):
    from langfuse.openai import AsyncOpenAI
    return AsyncOpenAI(api_key=api_key, base_url=base_url)


class LangfuseTurnHandle:
    def __init__(self, stack: ExitStack, span):
        self._stack = stack
        self._span = span
        self._output = None
        self._ended = False

    def set_output(self, text):
        self._output = text

    def end(self, error=None):
        if self._ended:
            return
        self._ended = True
        try:
            from langfuse import get_client
            if self._output is not None:
                self._span.update(output=self._output)
                get_client().set_current_trace_io(output=self._output)
            if error is not None:
                self._span.update(level='ERROR', status_message=str(error)[:500])
        except Exception as e:
            logger.warning(f'Langfuse: failed to finalize turn: {e}')
        finally:
            try:
                self._stack.close()
            except Exception as e:
                logger.warning(f'Langfuse: failed to close turn context: {e}')


def begin_turn(*, name, user_id, session_id, input_text=None, tags=()):
    stack = ExitStack()
    try:
        from langfuse import get_client, propagate_attributes
        # propagate_attributes must be the outer context so the root span
        # and everything nested (generations, tool spans, OTel Anthropic
        # spans, tasks spawned inside the turn) inherit user/session/tags.
        stack.enter_context(propagate_attributes(
            user_id=user_id,
            session_id=session_id,
            tags=list(tags) or None,
        ))
        span = stack.enter_context(get_client().start_as_current_observation(
            as_type='agent' if 'agent' in tags else 'span',
            name=name,
            input=input_text,
        ))
        if input_text:
            get_client().set_current_trace_io(input=input_text)
        return LangfuseTurnHandle(stack, span)
    except Exception as e:
        logger.warning(f'Langfuse: failed to begin turn: {e}')
        stack.close()
        return NoopTurnHandle()


class LangfuseSpanHandle:
    def __init__(self, span):
        self._span = span
        self._output = None

    def set_output(self, value):
        self._output = value


@contextmanager
def span(*, name, as_type, input=None):
    try:
        from langfuse import get_client
        cm = get_client().start_as_current_observation(as_type=as_type, name=name, input=input)
        s = cm.__enter__()
    except Exception as e:
        logger.warning(f'Langfuse: failed to start span {name!r}: {e}')
        yield NoopSpanHandle()
        return

    handle = LangfuseSpanHandle(s)
    try:
        yield handle
    except BaseException as e:
        try:
            s.update(level='ERROR', status_message=str(e)[:500])
        finally:
            cm.__exit__(type(e), e, e.__traceback__)
        raise
    else:
        try:
            if handle._output is not None:
                s.update(output=handle._output)
        finally:
            cm.__exit__(None, None, None)
