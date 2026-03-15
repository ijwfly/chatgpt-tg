import asyncio
import json
from datetime import datetime, timezone, timedelta

import settings
from app.openai_helpers.llm_client_factory import LLMClientFactory
from tests.helpers.mock_llm_client import MockLLMClient
from tests.helpers.telegram_factory import make_text_message
from tests.helpers.bot_spy import BotSpy


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


class TestScheduleTaskTool:

    async def test_schedule_once_task(self, bot_app):
        """Agent can schedule a one-time task via ScheduleTask tool."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 80001

        await _create_agent_user(telegram_bot, dp, user_id)

        mock_llm = MockLLMClient()
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_sched1',
                'function': {
                    'name': 'ScheduleTask',
                    'arguments': json.dumps({
                        'title': 'Reminder Test',
                        'prompt': 'Remind me to check email',
                        'schedule_type': 'once',
                        'when': 'in 2 hours',
                    }),
                },
            }],
        )
        mock_llm.add_response(content="Reminder scheduled!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Schedule a reminder', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.3)

        spy.assert_sent_text_contains("Reminder scheduled!")

        # Verify task saved to DB
        tasks = await telegram_bot.db.get_scheduled_tasks(user_id, enabled_only=True)
        assert len(tasks) == 1
        assert tasks[0]['title'] == 'Reminder Test'
        assert tasks[0]['schedule_type'] == 'once'
        assert tasks[0]['enabled'] is True

    async def test_schedule_recurring_task(self, bot_app):
        """Agent can schedule a recurring task with cron expression."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 80002

        await _create_agent_user(telegram_bot, dp, user_id)

        mock_llm = MockLLMClient()
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_sched2',
                'function': {
                    'name': 'ScheduleTask',
                    'arguments': json.dumps({
                        'title': 'Daily Standup',
                        'prompt': 'Generate a summary of yesterdays work',
                        'schedule_type': 'recurring',
                        'cron_expression': '0 10 * * *',
                    }),
                },
            }],
        )
        mock_llm.add_response(content="Daily task scheduled!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Schedule daily standup', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.3)

        spy.assert_sent_text_contains("Daily task scheduled!")

        tasks = await telegram_bot.db.get_scheduled_tasks(user_id)
        assert len(tasks) == 1
        assert tasks[0]['cron_expression'] == '0 10 * * *'
        assert tasks[0]['schedule_type'] == 'recurring'
        assert tasks[0]['next_execution'] is not None

    async def test_list_scheduled_tasks(self, bot_app):
        """Agent can list scheduled tasks via ListScheduledTasks tool."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 80003

        user = await _create_agent_user(telegram_bot, dp, user_id)

        # Create a task directly in DB
        future_time = datetime.now(timezone.utc) + timedelta(hours=1)
        await telegram_bot.db.create_scheduled_task(
            chat_id=user_id, user_id=user.id, title='Test Task',
            prompt='Do something', schedule_type='once',
            run_at=future_time, cron_expression=None, next_execution=future_time,
        )

        mock_llm = MockLLMClient()
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_list',
                'function': {
                    'name': 'ListScheduledTasks',
                    'arguments': '{}',
                },
            }],
        )
        mock_llm.add_response(content="Here are your tasks.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('List my tasks', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.3)

        spy.assert_sent_text_contains("Here are your tasks.")

        # Verify LLM received the task list
        assert len(mock_llm.calls) >= 2
        tool_result_messages = [
            m for m in mock_llm.calls[1]['messages'] if m.get('role') == 'tool'
        ]
        assert any('Test Task' in str(m.get('content', '')) for m in tool_result_messages)

    async def test_cancel_scheduled_task(self, bot_app):
        """Agent can cancel a scheduled task via CancelScheduledTask tool."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 80004

        user = await _create_agent_user(telegram_bot, dp, user_id)

        # Create a task
        future_time = datetime.now(timezone.utc) + timedelta(hours=1)
        record = await telegram_bot.db.create_scheduled_task(
            chat_id=user_id, user_id=user.id, title='To Cancel',
            prompt='Something', schedule_type='once',
            run_at=future_time, cron_expression=None, next_execution=future_time,
        )
        task_id = record['id']

        mock_llm = MockLLMClient()
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_cancel',
                'function': {
                    'name': 'CancelScheduledTask',
                    'arguments': json.dumps({'task_id': task_id}),
                },
            }],
        )
        mock_llm.add_response(content="Task cancelled.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Cancel that task', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.3)

        spy.assert_sent_text_contains("Task cancelled.")

        # Verify task is disabled in DB
        tasks = await telegram_bot.db.get_scheduled_tasks(user_id, enabled_only=True)
        assert len(tasks) == 0

    async def test_schedule_task_invalid_type(self, bot_app):
        """ScheduleTask returns error for invalid schedule_type."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 80005

        await _create_agent_user(telegram_bot, dp, user_id)

        mock_llm = MockLLMClient()
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_bad',
                'function': {
                    'name': 'ScheduleTask',
                    'arguments': json.dumps({
                        'title': 'Bad',
                        'prompt': 'Bad',
                        'schedule_type': 'invalid',
                    }),
                },
            }],
        )
        mock_llm.add_response(content="Got an error.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Schedule bad', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.3)

        # Verify error was returned to LLM
        assert len(mock_llm.calls) >= 2
        tool_results = [m for m in mock_llm.calls[1]['messages'] if m.get('role') == 'tool']
        assert any('Error' in str(m.get('content', '')) for m in tool_results)


class TestSchedulerServiceDB:

    async def test_get_due_tasks(self, db):
        """get_due_tasks returns tasks whose next_execution is in the past."""
        # Create a user first
        user = await db.create_user(99801, settings.USER_ROLE_DEFAULT)

        past_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        future_time = datetime.now(timezone.utc) + timedelta(hours=1)

        await db.create_scheduled_task(
            chat_id=99801, user_id=user.id, title='Due Task',
            prompt='Do it', schedule_type='once',
            run_at=past_time, cron_expression=None, next_execution=past_time,
        )
        await db.create_scheduled_task(
            chat_id=99801, user_id=user.id, title='Future Task',
            prompt='Later', schedule_type='once',
            run_at=future_time, cron_expression=None, next_execution=future_time,
        )

        due = await db.get_due_tasks(datetime.now(timezone.utc))
        assert len(due) == 1
        assert due[0]['title'] == 'Due Task'

    async def test_disable_scheduled_task(self, db):
        """Disabled tasks don't show up in enabled-only queries."""
        user = await db.create_user(99802, settings.USER_ROLE_DEFAULT)

        future_time = datetime.now(timezone.utc) + timedelta(hours=1)
        record = await db.create_scheduled_task(
            chat_id=99802, user_id=user.id, title='To Disable',
            prompt='Something', schedule_type='once',
            run_at=future_time, cron_expression=None, next_execution=future_time,
        )

        await db.disable_scheduled_task(record['id'])

        tasks = await db.get_scheduled_tasks(99802, enabled_only=True)
        assert len(tasks) == 0

    async def test_update_execution(self, db):
        """update_scheduled_task_execution updates timestamps."""
        user = await db.create_user(99803, settings.USER_ROLE_DEFAULT)

        now = datetime.now(timezone.utc)
        next_time = now + timedelta(days=1)
        record = await db.create_scheduled_task(
            chat_id=99803, user_id=user.id, title='Recurring',
            prompt='Do daily', schedule_type='recurring',
            run_at=None, cron_expression='0 10 * * *', next_execution=now,
        )

        await db.update_scheduled_task_execution(record['id'], now, next_time)

        tasks = await db.get_scheduled_tasks(99803)
        assert len(tasks) == 1
        assert tasks[0]['last_execution'] is not None
        assert tasks[0]['next_execution'] > now

    async def test_get_user_by_id(self, db):
        """get_user_by_id returns user by primary key."""
        user = await db.create_user(99804, settings.USER_ROLE_DEFAULT)
        found = await db.get_user_by_id(user.id)
        assert found is not None
        assert found.telegram_id == 99804

        not_found = await db.get_user_by_id(999999)
        assert not_found is None
