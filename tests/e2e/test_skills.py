import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

import settings
from app.openai_helpers.llm_client_factory import LLMClientFactory
from app.sandbox.client import SandboxError
from tests.helpers.bot_spy import BotSpy
from tests.helpers.fake_sandbox import (
    FakeSandboxClient, make_catalog, make_skill, patch_sandbox_client,
)
from tests.helpers.mock_llm_client import MockLLMClient
from tests.helpers.telegram_factory import make_text_message


@pytest.fixture(autouse=True)
def enable_skills():
    old_sandbox, old_skills = settings.ENABLE_BASH_SANDBOX, settings.ENABLE_SKILLS
    settings.ENABLE_BASH_SANDBOX = True
    settings.ENABLE_SKILLS = True
    yield
    settings.ENABLE_BASH_SANDBOX, settings.ENABLE_SKILLS = old_sandbox, old_skills


@pytest.fixture(autouse=True)
def fake_sandbox_client():
    with patch_sandbox_client() as client:
        yield client


async def _create_agent_user(telegram_bot, dp, user_id):
    mock_llm = MockLLMClient()
    mock_llm.add_response("Hello!")
    LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

    await dp.process_update(make_text_message('Hi', user_id=user_id))
    await asyncio.sleep(0.1)

    user = await telegram_bot.db.get_user(user_id)
    user.agent_mode = True
    user.use_functions = True
    await telegram_bot.db.update_user(user)
    return user


async def _run_turn(dp, user_id, text, response='Done.', sleep=0.3):
    """One agent turn with a single-response LLM. Returns the mock client."""
    mock_llm = MockLLMClient()
    mock_llm.add_response(response)
    LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

    await dp.process_update(make_text_message(text, user_id=user_id))
    await asyncio.sleep(sleep)
    return mock_llm


def _system_prompt(mock_llm, index=0):
    messages = mock_llm.calls[index]['messages']
    assert messages[0]['role'] == 'system'
    return str(messages[0]['content'])


class TestSkillsCatalog:

    async def test_catalog_reaches_agent_system_prompt(self, bot_app):
        """Names, descriptions and paths are in the prompt; skill bodies are not."""
        telegram_bot, dp, mock_bot = bot_app
        user_id = 95001

        await _create_agent_user(telegram_bot, dp, user_id)
        FakeSandboxClient.skills_result = make_catalog(
            skills=[
                make_skill('weekly-report', 'builds a weekly spending summary from a CSV statement',
                           telegram_id=user_id),
                make_skill('skill-creator', 'creating a new skill or editing an existing one',
                           scope='public'),
            ],
            telegram_id=user_id,
        )

        mock_llm = await _run_turn(dp, user_id, 'here is my statement')

        prompt = _system_prompt(mock_llm)
        assert '## Skills' in prompt
        assert '- weekly-report (personal): builds a weekly spending summary from a CSV statement ' \
               f'-> skills/weekly-report/SKILL.md' in prompt
        assert '- skill-creator (shared): creating a new skill or editing an existing one ' \
               '-> /workspace/public_skills/skill-creator/SKILL.md' in prompt
        # only the catalog travels in the prompt, never the instructions themselves
        assert 'frontmatter' not in prompt
        assert FakeSandboxClient.skills_calls == [user_id]

    async def test_personal_skill_shadows_public_one(self, bot_app):
        telegram_bot, dp, mock_bot = bot_app
        user_id = 95002

        await _create_agent_user(telegram_bot, dp, user_id)
        FakeSandboxClient.skills_result = make_catalog(
            skills=[
                make_skill('skill-creator', 'my own tweaked version', telegram_id=user_id),
                make_skill('skill-creator', 'the bundled one', scope='public'),
            ],
            telegram_id=user_id,
        )

        mock_llm = await _run_turn(dp, user_id, 'make me a skill')

        prompt = _system_prompt(mock_llm)
        assert prompt.count('- skill-creator') == 1
        assert 'my own tweaked version -> skills/skill-creator/SKILL.md' in prompt
        assert 'the bundled one' not in prompt

    async def test_empty_catalog_adds_no_block(self, bot_app):
        telegram_bot, dp, mock_bot = bot_app
        user_id = 95003

        await _create_agent_user(telegram_bot, dp, user_id)
        FakeSandboxClient.skills_result = make_catalog(telegram_id=user_id)

        mock_llm = await _run_turn(dp, user_id, 'hello there')

        assert '## Skills' not in _system_prompt(mock_llm)

    async def test_sandbox_failure_does_not_break_the_turn(self, bot_app):
        """The agent must answer even when the skills catalog cannot be loaded."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 95004

        await _create_agent_user(telegram_bot, dp, user_id)

        async def failing_list_skills(self, telegram_user_id):
            raise SandboxError('Sandbox unavailable: connection refused')

        with patch.object(FakeSandboxClient, 'list_skills', failing_list_skills):
            mock_llm = await _run_turn(dp, user_id, 'do something', response='Answered anyway.')

        spy.assert_sent_text_contains('Answered anyway.')
        assert '## Skills' not in _system_prompt(mock_llm)

    async def test_disabled_skills_are_not_requested(self, bot_app):
        telegram_bot, dp, mock_bot = bot_app
        user_id = 95005

        await _create_agent_user(telegram_bot, dp, user_id)
        FakeSandboxClient.skills_result = make_catalog(
            skills=[make_skill('weekly-report', 'a description', telegram_id=user_id)],
            telegram_id=user_id,
        )
        settings.ENABLE_SKILLS = False

        mock_llm = await _run_turn(dp, user_id, 'hello')

        assert FakeSandboxClient.skills_calls == []
        assert '## Skills' not in _system_prompt(mock_llm)

    async def test_catalog_is_capped_and_descriptions_truncated(self, bot_app):
        telegram_bot, dp, mock_bot = bot_app
        user_id = 95006

        await _create_agent_user(telegram_bot, dp, user_id)
        long_description = 'x' * (settings.SKILLS_MAX_DESCRIPTION_CHARS + 500)
        FakeSandboxClient.skills_result = make_catalog(
            skills=[make_skill(f'skill-{i:03d}', long_description, telegram_id=user_id)
                    for i in range(settings.SKILLS_MAX_COUNT + 10)],
            telegram_id=user_id,
        )

        mock_llm = await _run_turn(dp, user_id, 'hello')

        prompt = _system_prompt(mock_llm)
        skill_lines = [line for line in prompt.splitlines() if line.startswith('- skill-')]
        assert len(skill_lines) == settings.SKILLS_MAX_COUNT
        assert 'x' * (settings.SKILLS_MAX_DESCRIPTION_CHARS + 1) not in prompt
        assert 'x' * settings.SKILLS_MAX_DESCRIPTION_CHARS in prompt

    async def test_agent_can_read_skill_by_catalog_path(self, bot_app):
        """The path from the catalog is directly usable by read_file (level 2 loading)."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 95007

        await _create_agent_user(telegram_bot, dp, user_id)
        FakeSandboxClient.skills_result = make_catalog(
            skills=[make_skill('weekly-report', 'weekly spending summary from a CSV statement',
                               telegram_id=user_id)],
            telegram_id=user_id,
        )
        skill_body = '---\nname: weekly-report\ndescription: ...\n---\n\nGroup rows by week.'
        FakeSandboxClient.files['skills/weekly-report/SKILL.md'] = skill_body

        mock_llm = MockLLMClient()
        mock_llm.add_response(content=None, tool_calls=[{
            'id': 'call_read_skill',
            'function': {
                'name': 'read_file',
                'arguments': json.dumps({'path': 'skills/weekly-report/SKILL.md'}),
            },
        }])
        mock_llm.add_response('Grouped by week as the skill says.')
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        await dp.process_update(make_text_message('here is my statement', user_id=user_id))
        await asyncio.sleep(0.5)

        spy.assert_sent_text_contains('Grouped by week as the skill says.')
        tool_messages = [m for m in mock_llm.calls[1]['messages'] if m.get('role') == 'tool']
        assert any('Group rows by week.' in str(m.get('content', '')) for m in tool_messages), \
            f'skill body did not reach the context: {tool_messages}'

    async def test_sub_agent_gets_the_catalog(self, bot_app):
        telegram_bot, dp, mock_bot = bot_app
        user_id = 95008

        await _create_agent_user(telegram_bot, dp, user_id)
        FakeSandboxClient.skills_result = make_catalog(
            skills=[make_skill('weekly-report', 'weekly spending summary from a CSV statement',
                               telegram_id=user_id)],
            telegram_id=user_id,
        )

        mock_llm = MockLLMClient()
        mock_llm.add_response(content=None, tool_calls=[{
            'id': 'call_spawn',
            'function': {
                'name': 'SpawnTask',
                'arguments': json.dumps({'description': 'Subtask', 'prompt': 'Build the report'}),
            },
        }])
        mock_llm.add_response('Sub result')
        mock_llm.add_response('Task finished.')
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        await dp.process_update(make_text_message('analyze my statement', user_id=user_id))
        await asyncio.sleep(1.0)

        sub_calls = [
            c for c in mock_llm.calls
            if any(m.get('role') == 'user' and m.get('content') == 'Build the report'
                   for m in c['messages'])
        ]
        assert sub_calls, 'Sub-agent LLM call not found'
        sub_prompt = str(sub_calls[0]['messages'][0]['content'])
        assert 'You are a sub-agent' in sub_prompt
        assert '- weekly-report (personal):' in sub_prompt
