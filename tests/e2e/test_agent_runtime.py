import asyncio
import json

import pytest

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


class TestAgentRuntime:

    async def test_agent_mode_simple_response(self, bot_app):
        """Agent mode produces a normal response when no tools are called."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 70001

        await _create_agent_user(telegram_bot, dp, user_id)

        mock_llm = MockLLMClient()
        mock_llm.add_response("I'm the agent runtime responding.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Tell me something', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.2)

        spy.assert_sent_text_contains("I'm the agent runtime responding.")

    async def test_agent_mode_create_plan(self, bot_app):
        """Agent can create a plan via CreatePlan tool and gets the result back."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 70002

        await _create_agent_user(telegram_bot, dp, user_id)

        mock_llm = MockLLMClient()
        # LLM calls CreatePlan tool
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_plan_1',
                'function': {
                    'name': 'CreatePlan',
                    'arguments': json.dumps({
                        'title': 'Test Plan',
                        'steps': ['Step one', 'Step two', 'Step three'],
                    }),
                },
            }],
        )
        # LLM responds after getting plan result
        mock_llm.add_response(content="Plan created with 3 steps.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Create a plan for me', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.3)

        spy.assert_sent_text_contains("Plan created with 3 steps.")

        # Verify plan was saved to DB
        plan = await telegram_bot.db.get_active_plan(user_id)
        assert plan is not None
        assert plan['title'] == 'Test Plan'
        steps = plan['steps'] if isinstance(plan['steps'], list) else json.loads(plan['steps'])
        assert len(steps) == 3
        assert steps[0]['description'] == 'Step one'
        assert steps[0]['status'] == 'pending'

    async def test_agent_mode_update_plan_step(self, bot_app):
        """Agent can update plan steps and they persist in DB."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 70003

        await _create_agent_user(telegram_bot, dp, user_id)

        mock_llm = MockLLMClient()
        # Create plan then update step 1 in one turn
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_cp',
                'function': {
                    'name': 'CreatePlan',
                    'arguments': json.dumps({
                        'title': 'Update Test',
                        'steps': ['First step', 'Second step'],
                    }),
                },
            }],
        )
        # After plan created, update step 1
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_up',
                'function': {
                    'name': 'UpdatePlanStep',
                    'arguments': json.dumps({
                        'step_id': '1',
                        'status': 'completed',
                    }),
                },
            }],
        )
        mock_llm.add_response(content="Step 1 completed!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Work on the plan', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.3)

        spy.assert_sent_text_contains("Step 1 completed!")

        # Verify step status in DB
        plan = await telegram_bot.db.get_active_plan(user_id)
        steps = plan['steps'] if isinstance(plan['steps'], list) else json.loads(plan['steps'])
        assert steps[0]['status'] == 'completed'
        assert steps[1]['status'] == 'pending'

    async def test_agent_mode_plan_persists_across_turns(self, bot_app):
        """Plan created in one turn is available in the next turn."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 70004

        await _create_agent_user(telegram_bot, dp, user_id)

        # Turn 1: create plan
        mock_llm = MockLLMClient()
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_cp2',
                'function': {
                    'name': 'CreatePlan',
                    'arguments': json.dumps({
                        'title': 'Persistent Plan',
                        'steps': ['Do thing A', 'Do thing B'],
                    }),
                },
            }],
        )
        mock_llm.add_response(content="Plan created.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Make a plan', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.3)

        # Turn 2: get plan (should load from DB)
        mock_llm2 = MockLLMClient()
        mock_llm2.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_gp',
                'function': {
                    'name': 'GetPlan',
                    'arguments': '{}',
                },
            }],
        )
        mock_llm2.add_response(content="The plan has 2 steps.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm2

        update2 = make_text_message('What is the plan?', user_id=user_id)
        await dp.process_update(update2)
        await asyncio.sleep(0.3)

        spy.assert_sent_text_contains("The plan has 2 steps.")

        # Verify LLM received the plan content in the tool result
        assert len(mock_llm2.calls) == 2
        second_call_messages = mock_llm2.calls[1]['messages']
        tool_result_content = [
            m.get('content', '') for m in second_call_messages
            if m.get('role') == 'tool'
        ]
        assert any('Persistent Plan' in str(c) for c in tool_result_content)

    async def test_agent_mode_delete_plan(self, bot_app):
        """Agent can delete the active plan."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 70005

        await _create_agent_user(telegram_bot, dp, user_id)

        mock_llm = MockLLMClient()
        # Create plan then delete it
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_cp3',
                'function': {
                    'name': 'CreatePlan',
                    'arguments': json.dumps({
                        'title': 'To Delete',
                        'steps': ['Will be deleted'],
                    }),
                },
            }],
        )
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_dp',
                'function': {
                    'name': 'DeletePlan',
                    'arguments': '{}',
                },
            }],
        )
        mock_llm.add_response(content="Plan deleted.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Delete my plan', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.3)

        spy.assert_sent_text_contains("Plan deleted.")

        plan = await telegram_bot.db.get_active_plan(user_id)
        assert plan is None

    async def test_agent_mode_check_task_no_tasks(self, bot_app):
        """CheckTask with no background tasks returns appropriate message."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 70006

        await _create_agent_user(telegram_bot, dp, user_id)

        mock_llm = MockLLMClient()
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_ct',
                'function': {
                    'name': 'CheckTask',
                    'arguments': '{}',
                },
            }],
        )
        mock_llm.add_response(content="No tasks running.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Check tasks', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.3)

        spy.assert_sent_text_contains("No tasks running.")

        # Verify the tool result contained "No background tasks"
        assert len(mock_llm.calls) == 2
        second_call_messages = mock_llm.calls[1]['messages']
        tool_results = [m for m in second_call_messages if m.get('role') == 'tool']
        assert any('No background tasks' in str(m.get('content', '')) for m in tool_results)

    async def test_agent_mode_multiple_tool_calls_in_one_response(self, bot_app):
        """Agent handles multiple tool calls in a single LLM response."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 70007

        await _create_agent_user(telegram_bot, dp, user_id)

        mock_llm = MockLLMClient()
        # LLM calls CreatePlan and GetPlan in one response
        mock_llm.add_response(
            content=None,
            tool_calls=[
                {
                    'id': 'call_multi_1',
                    'function': {
                        'name': 'CreatePlan',
                        'arguments': json.dumps({
                            'title': 'Multi Plan',
                            'steps': ['Step A'],
                        }),
                    },
                },
                {
                    'id': 'call_multi_2',
                    'function': {
                        'name': 'CheckTask',
                        'arguments': '{}',
                    },
                },
            ],
        )
        mock_llm.add_response(content="Plan created and tasks checked.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Do both things', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.3)

        spy.assert_sent_text_contains("Plan created and tasks checked.")

    async def test_agent_mode_plan_auto_completes(self, bot_app):
        """Plan auto-completes when all steps are completed or skipped."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 70008

        await _create_agent_user(telegram_bot, dp, user_id)

        mock_llm = MockLLMClient()
        # Create 2-step plan, complete both
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_ac1',
                'function': {
                    'name': 'CreatePlan',
                    'arguments': json.dumps({
                        'title': 'Auto Complete',
                        'steps': ['Step 1', 'Step 2'],
                    }),
                },
            }],
        )
        mock_llm.add_response(
            content=None,
            tool_calls=[
                {
                    'id': 'call_ac2',
                    'function': {
                        'name': 'UpdatePlanStep',
                        'arguments': json.dumps({'step_id': '1', 'status': 'completed'}),
                    },
                },
                {
                    'id': 'call_ac3',
                    'function': {
                        'name': 'UpdatePlanStep',
                        'arguments': json.dumps({'step_id': '2', 'status': 'completed'}),
                    },
                },
            ],
        )
        mock_llm.add_response(content="All done!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Do everything', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.3)

        spy.assert_sent_text_contains("All done!")

        # Plan should be completed (no longer active)
        plan = await telegram_bot.db.get_active_plan(user_id)
        assert plan is None

    async def test_agent_mode_multiple_spawn_tasks(self, bot_app):
        """Multiple SpawnTask calls in one response all succeed (no false sub-agent detection)."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 70010

        await _create_agent_user(telegram_bot, dp, user_id)

        mock_llm = MockLLMClient()
        # LLM spawns 3 tasks simultaneously
        mock_llm.add_response(
            content=None,
            tool_calls=[
                {
                    'id': 'call_spawn_1',
                    'function': {
                        'name': 'SpawnTask',
                        'arguments': json.dumps({
                            'description': 'Task A',
                            'prompt': 'Do task A',
                        }),
                    },
                },
                {
                    'id': 'call_spawn_2',
                    'function': {
                        'name': 'SpawnTask',
                        'arguments': json.dumps({
                            'description': 'Task B',
                            'prompt': 'Do task B',
                        }),
                    },
                },
                {
                    'id': 'call_spawn_3',
                    'function': {
                        'name': 'SpawnTask',
                        'arguments': json.dumps({
                            'description': 'Task C',
                            'prompt': 'Do task C',
                        }),
                    },
                },
            ],
        )
        # Sub-agents will also call the LLM — queue responses for them
        mock_llm.add_response(content="Sub-agent A result")
        mock_llm.add_response(content="Sub-agent B result")
        mock_llm.add_response(content="Sub-agent C result")
        # After spawning, main agent says it's waiting
        mock_llm.add_response(content="Spawned 3 tasks, waiting for results.")
        # After bg results are drained, main agent gives final response
        mock_llm.add_response(content="All sub-agents completed.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Do 3 things in parallel', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(1.0)

        # Verify all 3 SpawnTask tool results contain "started" (not "Error")
        # The second call (index 1) to the LLM is after tool execution
        # Find the call that has tool results in it
        tool_result_calls = [
            c for c in mock_llm.calls
            if any(m.get('role') == 'tool' for m in c['messages'])
        ]
        assert len(tool_result_calls) >= 1
        first_tool_call = tool_result_calls[0]
        tool_results = [m for m in first_tool_call['messages'] if m.get('role') == 'tool']
        assert len(tool_results) == 3
        for tr in tool_results:
            assert 'started' in tr.get('content', ''), f"Expected 'started' but got: {tr.get('content')}"

    async def test_agent_mode_plan_sends_and_edits_message(self, bot_app):
        """Plan creation sends a message, step update edits it."""
        telegram_bot, dp, mock_bot = bot_app
        spy = BotSpy(mock_bot)
        user_id = 70011

        await _create_agent_user(telegram_bot, dp, user_id)

        mock_llm = MockLLMClient()
        # Create plan, then update a step
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_pv1',
                'function': {
                    'name': 'CreatePlan',
                    'arguments': json.dumps({
                        'title': 'Visual Plan',
                        'steps': ['Do X', 'Do Y'],
                    }),
                },
            }],
        )
        mock_llm.add_response(
            content=None,
            tool_calls=[{
                'id': 'call_pv2',
                'function': {
                    'name': 'UpdatePlanStep',
                    'arguments': json.dumps({'step_id': '1', 'status': 'completed'}),
                },
            }],
        )
        mock_llm.add_response(content="Step 1 done.")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Plan and execute', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.3)

        # Plan message should have been sent (sendMessage with plan text)
        all_sent = spy.get_all_sent_texts()
        plan_messages = [t for t in all_sent if 'Visual Plan' in t]
        assert len(plan_messages) >= 1, f"Expected plan message in sent texts: {all_sent}"

        # Plan message should have been edited (editMessageText with updated status)
        all_edited = spy.get_all_edited_texts()
        edited_plan = [t for t in all_edited if 'Visual Plan' in t]
        assert len(edited_plan) >= 1, f"Expected plan edit in edited texts: {all_edited}"
        # The edited version should show step 1 as completed
        assert any('completed' in t for t in edited_plan)

    async def test_agent_settings_toggle(self, bot_app):
        """Agent mode can be toggled via settings."""
        telegram_bot, dp, mock_bot = bot_app
        user_id = 70009

        # Create user
        mock_llm = MockLLMClient()
        mock_llm.add_response("Hi!")
        LLMClientFactory._model_clients['gpt-3.5-turbo'] = mock_llm

        update = make_text_message('Hi', user_id=user_id)
        await dp.process_update(update)
        await asyncio.sleep(0.1)

        # Verify default is off
        user = await telegram_bot.db.get_user(user_id)
        assert user.agent_mode is False

        # Toggle via settings
        from tests.helpers.telegram_factory import make_callback_query
        update_cb = make_callback_query('settings.agent_mode', message_id=9999, user_id=user_id)
        await dp.process_update(update_cb)
        await asyncio.sleep(0.1)

        user = await telegram_bot.db.get_user(user_id)
        assert user.agent_mode is True


class TestBackgroundTaskManager:

    async def test_spawn_and_drain(self):
        """Spawn a task and drain its notification after completion."""
        from app.runtime.background_task_manager import BackgroundTaskManager

        mgr = BackgroundTaskManager(timeout=5)

        async def _task():
            return "result_value"

        task_id = mgr.spawn(_task(), "test task")
        assert task_id is not None
        assert mgr.has_pending()

        # Wait for task to complete
        await mgr.wait_pending(timeout=2)

        notifications = mgr.drain_notifications()
        assert len(notifications) == 1
        assert notifications[0].task_id == task_id
        assert notifications[0].status == "completed"
        assert "result_value" in notifications[0].result

    async def test_spawn_error_task(self):
        """Task that raises an exception produces error notification."""
        from app.runtime.background_task_manager import BackgroundTaskManager

        mgr = BackgroundTaskManager(timeout=5)

        async def _failing_task():
            raise ValueError("something broke")

        task_id = mgr.spawn(_failing_task(), "failing task")
        await mgr.wait_pending(timeout=2)

        notifications = mgr.drain_notifications()
        assert len(notifications) == 1
        assert notifications[0].status == "error"
        assert "something broke" in notifications[0].result

    async def test_drain_empty(self):
        """Draining with no tasks returns empty list."""
        from app.runtime.background_task_manager import BackgroundTaskManager

        mgr = BackgroundTaskManager()
        assert mgr.drain_notifications() == []

    async def test_check_status(self):
        """Check returns status of specific and all tasks."""
        from app.runtime.background_task_manager import BackgroundTaskManager

        mgr = BackgroundTaskManager(timeout=5)

        async def _slow():
            await asyncio.sleep(10)

        task_id = mgr.spawn(_slow(), "slow task")
        status = mgr.check(task_id)
        assert "[running]" in status

        all_status = mgr.check()
        assert task_id in all_status

        assert "Error: Unknown task" in mgr.check("nonexistent")

        await mgr.cancel_all()

    async def test_cancel_all(self):
        """cancel_all stops running tasks."""
        from app.runtime.background_task_manager import BackgroundTaskManager

        mgr = BackgroundTaskManager(timeout=5)

        async def _long_task():
            await asyncio.sleep(100)

        mgr.spawn(_long_task(), "long task")
        assert mgr.has_pending()

        await mgr.cancel_all()
        assert not mgr.has_pending()

    async def test_multiple_tasks(self):
        """Multiple tasks can run and complete independently."""
        from app.runtime.background_task_manager import BackgroundTaskManager

        mgr = BackgroundTaskManager(timeout=5)

        async def _task_a():
            return "result_a"

        async def _task_b():
            return "result_b"

        mgr.spawn(_task_a(), "task a")
        mgr.spawn(_task_b(), "task b")

        await mgr.wait_pending(timeout=2)

        notifications = mgr.drain_notifications()
        assert len(notifications) == 2
        results = {n.result for n in notifications}
        assert "result_a" in results
        assert "result_b" in results


class TestPlanManager:

    async def test_create_and_get_plan(self, db):
        """Create a plan and retrieve it."""
        from app.runtime.plan_manager import PlanManager

        pm = PlanManager(db, chat_id=99901)
        result = await pm.create_plan("My Plan", ["Step 1", "Step 2"])

        assert "My Plan" in result
        assert "Step 1" in result
        assert "Step 2" in result

        plan_text = await pm.get_plan()
        assert "My Plan" in plan_text

    async def test_update_step(self, db):
        """Update a step status."""
        from app.runtime.plan_manager import PlanManager

        pm = PlanManager(db, chat_id=99902)
        await pm.create_plan("Update Plan", ["First", "Second"])

        result = await pm.update_step("1", "completed")
        assert "completed" in result

        # Verify in DB
        plan_record = await db.get_active_plan(99902)
        steps = plan_record['steps'] if isinstance(plan_record['steps'], list) else __import__('json').loads(plan_record['steps'])
        assert steps[0]['status'] == 'completed'
        assert steps[1]['status'] == 'pending'

    async def test_update_invalid_step(self, db):
        """Updating a nonexistent step returns error."""
        from app.runtime.plan_manager import PlanManager

        pm = PlanManager(db, chat_id=99903)
        await pm.create_plan("Error Plan", ["One"])

        result = await pm.update_step("999", "completed")
        assert "Error" in result

    async def test_update_invalid_status(self, db):
        """Updating with invalid status returns error."""
        from app.runtime.plan_manager import PlanManager

        pm = PlanManager(db, chat_id=99904)
        await pm.create_plan("Status Plan", ["One"])

        result = await pm.update_step("1", "invalid_status")
        assert "Error" in result

    async def test_delete_plan(self, db):
        """Delete a plan marks it as cancelled."""
        from app.runtime.plan_manager import PlanManager

        pm = PlanManager(db, chat_id=99905)
        await pm.create_plan("Delete Me", ["Step"])

        result = await pm.delete_plan()
        assert "cancelled" in result.lower()

        plan = await db.get_active_plan(99905)
        assert plan is None

    async def test_no_active_plan(self, db):
        """Operations on nonexistent plan return appropriate messages."""
        from app.runtime.plan_manager import PlanManager

        pm = PlanManager(db, chat_id=99906)
        await pm.load()

        assert "No active plan" in await pm.get_plan()
        assert "Error" in await pm.update_step("1", "completed")
        assert "No active plan" in await pm.delete_plan()

    async def test_auto_complete(self, db):
        """Plan auto-completes when all steps are completed."""
        from app.runtime.plan_manager import PlanManager

        pm = PlanManager(db, chat_id=99907)
        await pm.create_plan("Auto", ["A", "B"])

        await pm.update_step("1", "completed")
        await pm.update_step("2", "completed")

        plan = await db.get_active_plan(99907)
        assert plan is None  # No longer active (completed)

    async def test_plan_persists_and_loads(self, db):
        """Plan created by one PlanManager can be loaded by another."""
        from app.runtime.plan_manager import PlanManager

        pm1 = PlanManager(db, chat_id=99908)
        await pm1.create_plan("Persist Test", ["Step X"])

        pm2 = PlanManager(db, chat_id=99908)
        plan = await pm2.load()

        assert plan is not None
        assert plan.title == "Persist Test"
        assert len(plan.steps) == 1
        assert plan.steps[0].description == "Step X"

    async def test_new_plan_supersedes_old(self, db):
        """Creating a new plan deactivates the previous one."""
        from app.runtime.plan_manager import PlanManager

        pm = PlanManager(db, chat_id=99909)
        await pm.create_plan("Plan 1", ["Old step"])
        await pm.create_plan("Plan 2", ["New step"])

        plan = await db.get_active_plan(99909)
        assert plan['title'] == "Plan 2"
