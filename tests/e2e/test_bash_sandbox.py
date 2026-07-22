import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import types

import settings
from app.openai_helpers.llm_client_factory import LLMClientFactory
from app.sandbox.client import SandboxError
from tests.helpers.mock_llm_client import MockLLMClient
from tests.helpers.telegram_factory import make_text_message, make_document_message
from tests.helpers.bot_spy import BotSpy


@pytest.fixture(autouse=True)
def enable_sandbox():
    old = settings.ENABLE_BASH_SANDBOX
    settings.ENABLE_BASH_SANDBOX = True
    yield
    settings.ENABLE_BASH_SANDBOX = old


class FakeSandboxClient:
    """In-memory fake of app.sandbox.client.SandboxClient."""
    uploads = {}
    exec_results = []
    exec_calls = []
    download_result = None

    def __init__(self, base_url=None):
        pass

    @classmethod
    def reset(cls):
        cls.uploads = {}
        cls.exec_results = []
        cls.exec_calls = []
        cls.download_result = None

    async def exec(self, telegram_user_id, command, timeout):
        FakeSandboxClient.exec_calls.append({'user': telegram_user_id, 'command': command})
        if FakeSandboxClient.exec_results:
            return FakeSandboxClient.exec_results.pop(0)
        return {'stdout': '', 'stderr': '', 'exit_code': 0, 'cwd': '/workspace/user_test'}

    async def stat(self, telegram_user_id, path):
        if path in FakeSandboxClient.uploads:
            return {'type': 'file', 'size': len(FakeSandboxClient.uploads[path])}
        return {'type': 'missing'}

    async def upload_file(self, telegram_user_id, rel_path, data):
        FakeSandboxClient.uploads[rel_path] = data
        return {'status': 'ok', 'size': len(data), 'path': rel_path}

    async def download_file(self, telegram_user_id, rel_path, max_bytes):
        if FakeSandboxClient.download_result is None:
            raise SandboxError(f'Not found: {rel_path}')
        return FakeSandboxClient.download_result


@pytest.fixture(autouse=True)
def fake_sandbox_client():
    FakeSandboxClient.reset()
    with patch('app.functions.bash_sandbox.SandboxClient', FakeSandboxClient), \
            patch('app.bot.batched_input_handler.SandboxClient', FakeSandboxClient):
        yield FakeSandboxClient


async def _create_agent_user(telegram_bot, dp, user_id):
    """Helper: create a user with agent_mode enabled."""
    mock_llm = MockLLMClient()
    mock_llm.add_response("Hello!")
    LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

    update = make_text_message('Hi', user_id=user_id)
    await dp.process_update(update)
    await asyncio.sleep(0.1)

    user = await telegram_bot.db.get_user(user_id)
    user.agent_mode = True
    user.use_functions = True
    await telegram_bot.db.update_user(user)
    return user


def _mock_document_download(mock_bot, file_content=b'csv,data\n1,2\n'):
    """Patch get_file/download_file on the mock bot for document handling."""
    mock_bot.get_file = AsyncMock(return_value=types.File(
        file_id='test-doc-file-id',
        file_unique_id='unique-test-doc-file-id',
        file_size=len(file_content),
        file_path='documents/test-doc',
    ))

    async def fake_download(file_path, destination=None, **kwargs):
        with open(destination, 'wb') as f:
            f.write(file_content)

    mock_bot.download_file = AsyncMock(side_effect=fake_download)


class TestBashSandboxTools:

    async def test_bash_exec_tool(self, bot_app):
        """bash_exec tool result (stdout/exit_code) is passed back to the LLM."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 80001

        await _create_agent_user(telegram_bot, dp, user_id)

        FakeSandboxClient.exec_results.append({
            'stdout': 'hello-from-sandbox\n',
            'stderr': '',
            'exit_code': 0,
            'cwd': '/workspace/user_80001',
        })

        mock_llm = MockLLMClient()
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_bash_1',
                'function': {
                    'name': 'bash_exec',
                    'arguments': json.dumps({'command': 'echo hello-from-sandbox'}),
                },
            }],
        )
        mock_llm.add_response(content="Command executed.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Run echo', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.3)

        spy.assert_sent_text_contains("Command executed.")

        assert len(FakeSandboxClient.exec_calls) == 1
        assert FakeSandboxClient.exec_calls[0]['command'] == 'echo hello-from-sandbox'

        # tool result with stdout was passed back to the LLM
        assert len(mock_llm.calls) == 2
        tool_results = [m for m in mock_llm.calls[1]['messages'] if m.get('role') == 'tool']
        assert any('hello-from-sandbox' in str(m.get('content', '')) for m in tool_results)
        assert any('exit_code: 0' in str(m.get('content', '')) for m in tool_results)

    async def test_send_file_to_chat(self, bot_app):
        """send_file_to_chat downloads from sandbox and sends a Telegram document."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 80002

        await _create_agent_user(telegram_bot, dp, user_id)

        FakeSandboxClient.download_result = (b'a,b\n1,2\n', 'report.csv')

        mock_llm = MockLLMClient()
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_send_1',
                'function': {
                    'name': 'send_file_to_chat',
                    'arguments': json.dumps({'path': 'report.csv', 'caption': 'Your report'}),
                },
            }],
        )
        mock_llm.add_response(content="File delivered.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Send me the report', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.3)

        spy.assert_sent_text_contains("File delivered.")

        sent_documents = spy.get_calls_for_method('sendDocument')
        assert len(sent_documents) == 1
        assert sent_documents[0].get('caption') == 'Your report'

        # tool result confirms the send, agent loop continues
        tool_results = [m for m in mock_llm.calls[1]['messages'] if m.get('role') == 'tool']
        assert any('report.csv' in str(m.get('content', '')) for m in tool_results)

    async def test_sandbox_error_returned_to_llm(self, bot_app):
        """Sandbox failure becomes an 'Error: ...' tool result, processing survives."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 80003

        await _create_agent_user(telegram_bot, dp, user_id)

        # download_result stays None -> FakeSandboxClient raises SandboxError
        mock_llm = MockLLMClient()
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_send_err',
                'function': {
                    'name': 'send_file_to_chat',
                    'arguments': json.dumps({'path': 'missing.txt'}),
                },
            }],
        )
        mock_llm.add_response(content="The file does not exist.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Send missing file', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.3)

        spy.assert_sent_text_contains("The file does not exist.")

        tool_results = [m for m in mock_llm.calls[1]['messages'] if m.get('role') == 'tool']
        assert any('Error:' in str(m.get('content', '')) for m in tool_results)
        assert spy.get_calls_for_method('sendDocument') == []


class TestSandboxDocumentUpload:

    async def test_document_uploaded_to_workspace(self, bot_app):
        """In agent mode a document goes to the sandbox workspace and into context."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 80004

        await _create_agent_user(telegram_bot, dp, user_id)
        _mock_document_download(mock_bot, b'csv,data\n1,2\n')

        update = make_document_message('my report.csv', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.3)

        # filename is sanitized (space -> underscore) and uploaded
        assert 'my_report.csv' in FakeSandboxClient.uploads
        assert FakeSandboxClient.uploads['my_report.csv'] == b'csv,data\n1,2\n'
        spy.assert_sent_text_contains('Saved to agent workspace: my_report.csv')

        # next prompt: LLM sees the workspace file notice in context
        mock_llm = MockLLMClient()
        mock_llm.add_response("I can see your file.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update2 = make_text_message('What did I send you?', user_id=user_id)
        await dp.process_update(update2)
        await asyncio.sleep(0.3)

        all_contents = [str(m.get('content', '')) for m in mock_llm.calls[0]['messages']]
        assert any('[file uploaded to agent workspace] my_report.csv' in c for c in all_contents)

    async def test_document_cyrillic_name_preserved(self, bot_app):
        """Unicode (cyrillic) filenames are kept, only unsafe chars are replaced."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 80008

        await _create_agent_user(telegram_bot, dp, user_id)
        _mock_document_download(mock_bot, b'pdf-bytes')

        update = make_document_message('Собеседование CTO (финал).pdf', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.3)

        assert 'Собеседование_CTO__финал_.pdf' in FakeSandboxClient.uploads
        spy.assert_sent_text_contains('Saved to agent workspace: Собеседование_CTO__финал_.pdf')

    async def test_document_name_collision_gets_suffix(self, bot_app):
        """Uploading a file with an existing name gets a numeric suffix."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 80005

        await _create_agent_user(telegram_bot, dp, user_id)
        _mock_document_download(mock_bot, b'first')

        update = make_document_message('data.txt', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.3)

        _mock_document_download(mock_bot, b'second')
        update2 = make_document_message('data.txt', user_id=user_id)
        await dp.process_update(update2)
        await asyncio.sleep(0.3)

        assert FakeSandboxClient.uploads['data.txt'] == b'first'
        assert FakeSandboxClient.uploads['data_1.txt'] == b'second'
        spy.assert_sent_text_contains('Saved to agent workspace: data_1.txt')

    async def test_document_without_agent_mode_keeps_old_behavior(self, bot_app):
        """With agent_mode off documents follow the old (Vectara) path."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 80006

        # create a user without agent mode
        mock_llm = MockLLMClient()
        mock_llm.add_response("Hello!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm
        update = make_text_message('Hi', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.1)

        _mock_document_download(mock_bot)
        update2 = make_document_message('doc.pdf', user_id=user_id)
        await dp.process_update(update2)
        await asyncio.sleep(0.3)

        # VECTARA_RAG_ENABLED is False in tests -> old path replies this
        spy.assert_sent_text_contains('Documents are not supported')
        assert FakeSandboxClient.uploads == {}

    async def test_document_upload_sandbox_error(self, bot_app):
        """Sandbox failure during upload is reported to the user, no crash."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 80007

        await _create_agent_user(telegram_bot, dp, user_id)
        _mock_document_download(mock_bot)

        async def failing_upload(self, telegram_user_id, rel_path, data):
            raise SandboxError('Sandbox unavailable: connection refused')

        with patch.object(FakeSandboxClient, 'upload_file', failing_upload):
            update = make_document_message('doc.txt', user_id=user_id)
            await dp.process_update(update)
            await asyncio.sleep(0.3)

        spy.assert_sent_text_contains('Failed to save document to agent workspace')
