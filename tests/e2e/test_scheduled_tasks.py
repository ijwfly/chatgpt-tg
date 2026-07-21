import asyncio
import json
from datetime import datetime, timezone, timedelta

import pytz

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


class TestScheduledTaskTimezone:

    async def test_once_task_uses_configured_timezone(self, bot_app):
        """'tomorrow at 10:00' with USER_TIMEZONE=Europe/Moscow schedules 10:00 MSK (07:00 UTC)."""
        telegram_bot, dp, mock_bot = bot_app
        user_id = 80008
        moscow = pytz.timezone('Europe/Moscow')

        await _create_agent_user(telegram_bot, dp, user_id)

        original_tz = settings.USER_TIMEZONE
        settings.USER_TIMEZONE = 'Europe/Moscow'
        try:
            mock_llm = MockLLMClient()
            mock_llm.add_response(
                content=None,
                tool_calls=[{
                    'id': 'call_tz1',
                    'function': {
                        'name': 'ScheduleTask',
                        'arguments': json.dumps({
                            'title': 'Morning Task',
                            'prompt': 'Do the morning thing',
                            'schedule_type': 'once',
                            'when': 'tomorrow at 10:00',
                        }),
                    },
                }],
            )
            mock_llm.add_response(content="Scheduled!")
            LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

            update = make_text_message('Schedule for tomorrow morning', user_id=user_id)
            await dp.process_update(update)
            await asyncio.sleep(0.3)

            tasks = await telegram_bot.db.get_scheduled_tasks(user_id)
            assert len(tasks) == 1
            next_execution = tasks[0]['next_execution']

            local = next_execution.astimezone(moscow)
            assert (local.hour, local.minute) == (10, 0)
            assert local.date() == (datetime.now(moscow) + timedelta(days=1)).date()
            # Moscow is UTC+3, so the stored UTC instant is 07:00
            assert next_execution.astimezone(timezone.utc).hour == 7

            # Tool result reports the time in the configured zone
            tool_results = [m for m in mock_llm.calls[1]['messages'] if m.get('role') == 'tool']
            assert any('(Europe/Moscow)' in str(m.get('content', '')) for m in tool_results)
        finally:
            settings.USER_TIMEZONE = original_tz

    async def test_recurring_task_cron_in_configured_timezone(self, bot_app):
        """Cron '0 10 * * *' with USER_TIMEZONE=Europe/Moscow means 10:00 MSK, not UTC."""
        telegram_bot, dp, mock_bot = bot_app
        user_id = 80009
        moscow = pytz.timezone('Europe/Moscow')

        await _create_agent_user(telegram_bot, dp, user_id)

        original_tz = settings.USER_TIMEZONE
        settings.USER_TIMEZONE = 'Europe/Moscow'
        try:
            mock_llm = MockLLMClient()
            mock_llm.add_response(
                content=None,
                tool_calls=[{
                    'id': 'call_tz2',
                    'function': {
                        'name': 'ScheduleTask',
                        'arguments': json.dumps({
                            'title': 'Daily Local',
                            'prompt': 'Daily thing',
                            'schedule_type': 'recurring',
                            'cron_expression': '0 10 * * *',
                        }),
                    },
                }],
            )
            mock_llm.add_response(content="Scheduled daily!")
            LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

            update = make_text_message('Schedule daily task', user_id=user_id)
            await dp.process_update(update)
            await asyncio.sleep(0.3)

            tasks = await telegram_bot.db.get_scheduled_tasks(user_id)
            assert len(tasks) == 1
            local = tasks[0]['next_execution'].astimezone(moscow)
            assert (local.hour, local.minute) == (10, 0)
        finally:
            settings.USER_TIMEZONE = original_tz


class TestScheduledTaskContext:

    async def test_schedule_task_stores_context_snapshot(self, bot_app):
        """ScheduleTask stores the dialog branch it was created in, trimmed of the tool call itself."""
        telegram_bot, dp, mock_bot = bot_app
        user_id = 80006

        await _create_agent_user(telegram_bot, dp, user_id)

        mock_llm = MockLLMClient()
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_ctx1',
                'function': {
                    'name': 'ScheduleTask',
                    'arguments': json.dumps({
                        'title': 'Context Test',
                        'prompt': 'Do the thing we discussed',
                        'schedule_type': 'once',
                        'when': 'in 2 hours',
                    }),
                },
            }],
        )
        mock_llm.add_response(content="Scheduled with context!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Schedule the thing we discussed', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.3)

        tasks = await telegram_bot.db.get_scheduled_tasks(user_id)
        assert len(tasks) == 1
        context_ids = tasks[0]['context_message_ids']
        assert context_ids, "context snapshot must be stored on the task"

        # Snapshot holds the conversation branch: Hi / Hello! / the scheduling request,
        # with the trailing ScheduleTask tool-call message trimmed off
        messages = await telegram_bot.db.get_messages_by_ids(context_ids)
        assert len(messages) == 3
        assert messages[-1].message.role == 'user'
        assert 'Schedule the thing we discussed' in str(messages[-1].message.content)
        assert not messages[-1].message.tool_calls

    async def test_scheduled_task_fires_with_creation_context(self, bot_app):
        """When a task fires, the LLM sees the conversation the task was created in,
        and the result is persisted into that dialog branch."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 80007

        user = await _create_agent_user(telegram_bot, dp, user_id)

        # Conversation with a distinctive fact, then scheduling
        mock_llm = MockLLMClient()
        mock_llm.add_response(content="Noted, turquoise it is.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm
        update = make_text_message('My favorite color is turquoise', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.2)

        mock_llm = MockLLMClient()
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_ctx2',
                'function': {
                    'name': 'ScheduleTask',
                    'arguments': json.dumps({
                        'title': 'Color Reminder',
                        'prompt': 'Remind me about my favorite color',
                        'schedule_type': 'once',
                        'when': 'in 2 hours',
                    }),
                },
            }],
        )
        mock_llm.add_response(content="Will remind you!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Remind me about it later', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.3)

        tasks = await telegram_bot.db.get_scheduled_tasks(user_id)
        assert len(tasks) == 1
        context_ids = tasks[0]['context_message_ids']
        assert context_ids

        # Fire the task directly (bypassing the poll loop)
        fire_llm = MockLLMClient()
        fire_llm.add_response(content="Your favorite color is turquoise!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = fire_llm

        await telegram_bot.scheduler_service._execute_task(tasks[0])
        await asyncio.sleep(0.2)

        # The LLM saw the creation-time conversation
        assert len(fire_llm.calls) == 1
        llm_messages_str = str(fire_llm.calls[0]['messages'])
        assert 'turquoise' in llm_messages_str
        assert 'Remind me about my favorite color' in llm_messages_str
        # The trigger is explicitly marked as an execution, not a scheduling request
        assert '<scheduled_task_execution>' in llm_messages_str

        # Notification and result were sent to the chat
        spy.assert_sent_text_contains("⏰ Scheduled task: Color Reminder")
        spy.assert_sent_text_contains("Your favorite color is turquoise!")

        # The result is persisted as a continuation of the creation branch,
        # so the user can reply to it and keep the conversation going
        last_message = await telegram_bot.db.get_last_message(user.id, user_id)
        assert 'Your favorite color is turquoise!' in str(last_message.message.content)
        assert last_message.tg_message_id > 0
        assert set(context_ids).issubset(set(last_message.previous_message_ids))

        # One-time task got disabled after firing
        tasks_after = await telegram_bot.db.get_scheduled_tasks(user_id, enabled_only=True)
        assert len(tasks_after) == 0


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

    async def test_create_task_with_context_message_ids(self, db):
        """context_message_ids round-trips through the DB; omitted defaults to empty."""
        user = await db.create_user(99805, settings.USER_ROLE_DEFAULT)

        future_time = datetime.now(timezone.utc) + timedelta(hours=1)
        record = await db.create_scheduled_task(
            chat_id=99805, user_id=user.id, title='With Context',
            prompt='Something', schedule_type='once',
            run_at=future_time, cron_expression=None, next_execution=future_time,
            context_message_ids=[101, 102, 103],
        )
        assert record['context_message_ids'] == [101, 102, 103]

        record_no_ctx = await db.create_scheduled_task(
            chat_id=99805, user_id=user.id, title='No Context',
            prompt='Something', schedule_type='once',
            run_at=future_time, cron_expression=None, next_execution=future_time,
        )
        assert record_no_ctx['context_message_ids'] == []

    async def test_get_user_by_id(self, db):
        """get_user_by_id returns user by primary key."""
        user = await db.create_user(99804, settings.USER_ROLE_DEFAULT)
        found = await db.get_user_by_id(user.id)
        assert found is not None
        assert found.telegram_id == 99804

        not_found = await db.get_user_by_id(999999)
        assert not_found is None
