"""No-op observability backend, used when Langfuse is disabled. Must not import langfuse."""
from contextlib import contextmanager

import openai


class NoopTurnHandle:
    def set_output(self, text):
        pass

    def end(self, error=None):
        pass


class NoopSpanHandle:
    def set_output(self, value):
        pass


_TURN = NoopTurnHandle()
_SPAN = NoopSpanHandle()


def init():
    pass


def shutdown():
    pass


def create_openai_client(api_key, base_url=None):
    return openai.AsyncOpenAI(api_key=api_key, base_url=base_url)


def begin_turn(*, name, user_id, session_id, input_text=None, tags=()):
    return _TURN


@contextmanager
def span(*, name, as_type, input=None):
    yield _SPAN
