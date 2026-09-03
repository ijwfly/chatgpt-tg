"""Observability facade: with Langfuse disabled everything must no-op cleanly
and the langfuse package must never even be imported (removability guard)."""
import sys

import openai
import pytest

import settings
from app import observability


pytestmark = pytest.mark.skipif(
    settings.LANGFUSE_ENABLED,
    reason='these tests cover the disabled (no-op) backend',
)


class _Session:
    chat_id = 111


class _DialogManager:
    def __init__(self, messages):
        self.messages = messages


class _ContextManager:
    def __init__(self, messages):
        self.dialog_manager = _DialogManager(messages)


class _Msg:
    def __init__(self, id):
        self.id = id


class _TextInput:
    def __init__(self, text):
        self.text = text


class _Voice:
    def __init__(self, text):
        self.text = text


class _File:
    def __init__(self, filename, caption=None):
        self.filename = filename
        self.caption = caption


class _UserInput:
    def __init__(self, text_inputs=(), voice_transcriptions=(), sandbox_files=()):
        self.text_inputs = list(text_inputs)
        self.voice_transcriptions = list(voice_transcriptions)
        self.sandbox_files = list(sandbox_files)


class TestNoopBackend:

    def test_turn_lifecycle_is_safe(self):
        turn = observability.begin_turn(
            name='default-turn', user_id='1', session_id='111:5',
            input_text='hi', tags=('default',),
        )
        turn.set_output('answer')
        turn.end()
        turn.end()  # idempotent
        turn.end(error=ValueError('boom'))

    def test_spans_pass_through_and_reraise(self):
        with observability.tool_span('tool:test', input='{}') as span:
            span.set_output('ok')
        with observability.agent_span('sub-agent', input='task') as span:
            span.set_output('ok')

        with pytest.raises(RuntimeError):
            with observability.tool_span('tool:test'):
                raise RuntimeError('tool failed')

    def test_init_shutdown_are_safe(self):
        observability.init()
        observability.shutdown()

    def test_create_openai_client_returns_plain_client(self):
        client = observability.create_openai_client('key', base_url='http://localhost')
        assert type(client) is openai.AsyncOpenAI

    def test_langfuse_is_never_imported(self):
        turn = observability.begin_turn(
            name='default-turn', user_id='1', session_id=None, input_text='hi',
        )
        with observability.tool_span('tool:test') as span:
            span.set_output('ok')
        turn.end()
        observability.create_openai_client('key')
        assert 'langfuse' not in sys.modules


class TestTurnHelpers:

    def test_session_id_uses_dialog_root(self):
        cm = _ContextManager([_Msg(5), _Msg(6)])
        assert observability.turn_session_id(_Session(), cm) == '111:5'

    def test_session_id_none_for_empty_dialog(self):
        assert observability.turn_session_id(_Session(), _ContextManager([])) is None

    def test_input_text_concatenates_all_inputs(self):
        user_input = _UserInput(
            text_inputs=[_TextInput('hello'), _TextInput('')],
            voice_transcriptions=[_Voice('voice text')],
            sandbox_files=[_File('a.csv', caption='data'), _File('b.txt')],
        )
        text = observability.turn_input_text(user_input)
        assert text == 'hello\nvoice text\n[file: a.csv] data\n[file: b.txt]'
