"""Tool status hints: title plus a short detail of the actual call."""

import json

import pytest

import settings
from app.functions.base import build_status_message
from app.functions.agent_tools import (
    CancelScheduledTask, CreatePlan, ScheduleTask, SpawnTask, UpdatePlanStep, WaitTask,
)
from app.functions.bash_sandbox import BashExec, EditFile, ReadFile, SendFileToChat, WriteFile
from app.functions.dalle_3 import GenerateImageDalle3
from app.functions.mcp.mcp_function_storage import MCPFunction
from app.functions.save_user_settings import SaveUserSettings
from app.functions.web_agents import WebScraperAgent, WebSearchAgent
from app.functions.wolframalpha import QueryWolframAlpha


def status(function_class, args):
    return build_status_message(function_class, args if isinstance(args, str) else json.dumps(args))


class TestSimpleDetails:

    @pytest.mark.parametrize('function_class, args, expected', [
        (BashExec, {'command': 'echo hi', 'timeout': 60}, 'Running bash command: echo hi'),
        (ReadFile, {'path': 'data/report.csv'}, 'Reading file: data/report.csv'),
        (WriteFile, {'path': 'out.txt', 'content': 'x' * 500}, 'Writing file: out.txt'),
        (EditFile, {'path': 'main.py', 'old_text': 'a', 'new_text': 'b'}, 'Editing file: main.py'),
        (SendFileToChat, {'path': 'report.pdf'}, 'Sending file: report.pdf'),
        (WebSearchAgent, {'query': 'python asyncio tutorial'},
         'Searching the web: python asyncio tutorial'),
        (WebScraperAgent, {'url': 'https://example.com/post', 'task': 'summarize'},
         'Reading web page: https://example.com/post'),
        (QueryWolframAlpha, {'query': 'integral of x^2'}, 'Querying WolframAlpha: integral of x^2'),
        (GenerateImageDalle3, {'image_prompt': 'a cat in a spacesuit'},
         'Generating image: a cat in a spacesuit'),
        (SpawnTask, {'description': 'collect Q3 numbers', 'prompt': 'long prompt here'},
         'Spawning sub-agent: collect Q3 numbers'),
    ])
    def test_detail_comes_from_the_most_descriptive_param(self, function_class, args, expected):
        assert status(function_class, args) == expected

    def test_long_content_params_are_not_used_as_detail(self):
        """write_file shows the path, never the file content."""
        result = status(WriteFile, {'path': 'out.txt', 'content': 'SECRET ' * 100})
        assert result == 'Writing file: out.txt'


class TestCleanup:

    def test_multiline_command_collapses_to_one_line(self):
        result = status(BashExec, {'command': 'cd /workspace\n  && ls -la\n  && pwd'})
        assert result == 'Running bash command: cd /workspace && ls -la && pwd'

    def test_long_detail_is_truncated(self):
        long_query = 'word ' * 50
        result = status(WebSearchAgent, {'query': long_query})
        detail = result.split(': ', 1)[1]
        assert detail.endswith('…')
        assert len(detail) <= settings.FUNCTION_HINT_DETAIL_MAX_CHARS + 1

    def test_truncation_limit_is_configurable(self, monkeypatch):
        monkeypatch.setattr(settings, 'FUNCTION_HINT_DETAIL_MAX_CHARS', 10)
        assert status(WebSearchAgent, {'query': 'abcdefghijklmnop'}) == 'Searching the web: abcdefghij…'


class TestCompositeDetails:

    def test_create_plan_shows_title_and_step_count(self):
        assert status(CreatePlan, {'title': 'Q3 report', 'steps': ['a', 'b', 'c']}) == \
            'Creating plan: Q3 report (3 steps)'

    def test_create_plan_without_steps_shows_title(self):
        assert status(CreatePlan, {'title': 'Q3 report'}) == 'Creating plan: Q3 report'

    def test_update_plan_step_shows_transition(self):
        assert status(UpdatePlanStep, {'step_id': '2', 'status': 'completed'}) == \
            'Updating plan: step 2 → completed'

    def test_schedule_task_shows_title_and_time(self):
        assert status(ScheduleTask, {'title': 'backup', 'schedule_type': 'once',
                                     'prompt': 'do it', 'when': 'tomorrow at 9'}) == \
            'Scheduling task: backup (tomorrow at 9)'

    def test_schedule_task_falls_back_to_cron(self):
        assert status(ScheduleTask, {'title': 'digest', 'schedule_type': 'recurring',
                                     'prompt': 'do it', 'cron_expression': '0 10 * * *'}) == \
            'Scheduling task: digest (0 10 * * *)'

    def test_cancel_scheduled_task_shows_id(self):
        assert status(CancelScheduledTask, {'task_id': 12}) == 'Cancelling scheduled task: #12'


class TestNoDetail:

    def test_tool_without_params_keeps_its_title(self):
        assert status(WaitTask, {}) == 'Waiting for background tasks...'

    def test_save_user_settings_keeps_its_title(self):
        """The settings text is echoed to the user separately, so it stays out of the hint."""
        assert status(SaveUserSettings, {'settings_text': 'likes tea'}) == 'Saving user info...'

    @pytest.mark.parametrize('args', ['{not json', '[1, 2]', 'null', '""', '', '{}'])
    def test_unusable_arguments_fall_back_to_the_title(self, args):
        assert status(BashExec, args) == 'Running bash command...'

    def test_empty_detail_value_falls_back_to_the_title(self):
        assert status(BashExec, {'command': '   '}) == 'Running bash command...'

    def test_boolean_is_not_a_detail(self):
        assert status(BashExec, {'command': True}) == 'Running bash command...'


class TestMCPTools:

    def _tool(self, name='search_docs'):
        return MCPFunction('http://mcp.example', name, 'description',
                           {'type': 'object', 'properties': {}}, None)

    def test_preferred_key_wins(self):
        tool = self._tool()
        assert status(tool, {'limit': 5, 'query': 'vector index'}) == \
            'Running search docs: vector index'

    def test_falls_back_to_the_first_scalar_value(self):
        tool = self._tool()
        assert status(tool, {'filters': {'a': 1}, 'slug': 'my-doc'}) == \
            'Running search docs: my-doc'

    def test_no_scalar_values_keeps_the_title(self):
        tool = self._tool()
        assert status(tool, {'filters': {'a': 1}, 'tags': ['x']}) == 'Running search docs...'
