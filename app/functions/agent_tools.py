from dataclasses import dataclass
from typing import Optional, List, ClassVar

import pydantic
from pydantic import Field

from app.functions.base import OpenAIFunction, OpenAIFunctionParams
from app.runtime.background_task_manager import BackgroundTaskManager
from app.runtime.plan_manager import PlanManager
from app.runtime.side_effects import SideEffectHandler


@dataclass
class AgentToolContext:
    bg_manager: BackgroundTaskManager
    plan_manager: PlanManager
    sub_agent_runner: object  # callable: async def(prompt) -> str


class AgentFunction(OpenAIFunction):
    """Base class for agent tools. Accesses per-turn context via _agent_context class var."""
    _agent_context: ClassVar[Optional[AgentToolContext]] = None

    @property
    def agent_ctx(self) -> AgentToolContext:
        ctx = self.__class__._agent_context
        if ctx is None:
            raise RuntimeError("AgentToolContext not set")
        return ctx


# --- SpawnTask ---

class SpawnTaskParams(OpenAIFunctionParams):
    description: str = Field(..., description="Short description of what the background task should accomplish")
    prompt: str = Field(..., description="Detailed prompt for the background sub-agent")


class SpawnTask(AgentFunction):
    PARAMS_SCHEMA = SpawnTaskParams

    async def run(self, params: SpawnTaskParams) -> Optional[str]:
        async def _run_sub_agent():
            return await self.agent_ctx.sub_agent_runner(params.prompt)

        task_id = self.agent_ctx.bg_manager.spawn(_run_sub_agent(), params.description)
        return f"Background task {task_id} started: {params.description}"

    @classmethod
    def get_description(cls) -> str:
        return "Spawn a background sub-agent that runs autonomously with its own tool access. Returns task_id immediately. Use WaitTask to block until a task completes, or results will be delivered automatically."

    @classmethod
    def get_system_prompt_addition(cls) -> Optional[str]:
        return (
            "You can spawn background sub-agents using SpawnTask. Each sub-agent gets its own LLM call loop "
            "with tool access and works independently. Use this for parallelizable work — research, analysis, "
            "or any task that can run concurrently. Results are automatically delivered to you via "
            "<background-results> messages. When you receive background results, you MUST update the "
            "corresponding plan steps using UpdatePlanStep before responding to the user."
        )


# --- WaitTask ---

class WaitTaskParams(OpenAIFunctionParams):
    pass


class WaitTask(AgentFunction):
    PARAMS_SCHEMA = WaitTaskParams

    async def run(self, params: WaitTaskParams) -> Optional[str]:
        bg = self.agent_ctx.bg_manager
        if bg.has_pending():
            await bg.wait_for_any()
        return bg.check()

    @classmethod
    def get_description(cls) -> str:
        return "Wait for any background task to complete. Blocks until at least one running task finishes, then returns status of all tasks. If no tasks are running, returns immediately."


# --- CreatePlan ---

class CreatePlanParams(OpenAIFunctionParams):
    title: str = Field(..., description="Title of the plan")
    steps: List[str] = Field(..., description="List of step descriptions")


class CreatePlan(AgentFunction):
    PARAMS_SCHEMA = CreatePlanParams

    async def run(self, params: CreatePlanParams) -> Optional[str]:
        return await self.agent_ctx.plan_manager.create_plan(params.title, params.steps)

    @classmethod
    def get_description(cls) -> str:
        return "Create an execution plan with numbered steps. Replaces any existing active plan."

    @classmethod
    def get_system_prompt_addition(cls) -> Optional[str]:
        return (
            "You have plan management tools. When given a complex task, create a plan first with CreatePlan, "
            "then work through it step by step, updating each step's status with UpdatePlanStep as you progress. "
            "IMPORTANT: Always keep the plan up to date. When you complete a step or receive results from a "
            "background sub-agent for a step, immediately call UpdatePlanStep to mark it as 'completed'. "
            "When starting work on a step, mark it 'in_progress'. Never respond to the user without first "
            "updating all affected plan steps. Valid statuses: pending, in_progress, completed, skipped."
        )


# --- UpdatePlanStep ---

class UpdatePlanStepParams(OpenAIFunctionParams):
    step_id: str = Field(..., description="Step ID (e.g. '1', '2')")
    status: str = Field(..., description="New status: pending, in_progress, completed, or skipped")


class UpdatePlanStep(AgentFunction):
    PARAMS_SCHEMA = UpdatePlanStepParams

    async def run(self, params: UpdatePlanStepParams) -> Optional[str]:
        return await self.agent_ctx.plan_manager.update_step(params.step_id, params.status)

    @classmethod
    def get_description(cls) -> str:
        return "Update a plan step's status. The plan auto-completes when all steps are completed or skipped."


# --- GetPlan ---

class GetPlanParams(OpenAIFunctionParams):
    pass


class GetPlan(AgentFunction):
    PARAMS_SCHEMA = GetPlanParams

    async def run(self, params: GetPlanParams) -> Optional[str]:
        return await self.agent_ctx.plan_manager.get_plan()

    @classmethod
    def get_description(cls) -> str:
        return "Retrieve the current execution plan with all steps and their statuses."


# --- DeletePlan ---

class DeletePlanParams(OpenAIFunctionParams):
    pass


class DeletePlan(AgentFunction):
    PARAMS_SCHEMA = DeletePlanParams

    async def run(self, params: DeletePlanParams) -> Optional[str]:
        return await self.agent_ctx.plan_manager.delete_plan()

    @classmethod
    def get_description(cls) -> str:
        return "Cancel and delete the current active plan."


# --- ScheduleTask ---

class ScheduleTaskParams(OpenAIFunctionParams):
    title: str = Field(..., description="Short title for the scheduled task")
    prompt: str = Field(..., description="Natural language description of what to do when the task fires")
    schedule_type: str = Field(..., description="'once' for one-time or 'recurring' for repeated execution")
    when: Optional[str] = Field(None, description="For one-time tasks: natural language time expression, passed directly from user (e.g. 'через 5 минут', 'следующий вторник в 10:00', 'tomorrow at 9am')")
    cron_expression: Optional[str] = Field(None, description="Cron expression for recurring tasks (e.g. '0 10 * * *' for daily at 10:00)")


class ScheduleTask(OpenAIFunction):
    PARAMS_SCHEMA = ScheduleTaskParams

    async def run(self, params: ScheduleTaskParams) -> Optional[str]:
        from datetime import datetime, timezone
        from croniter import croniter
        import dateparser

        chat_id = self.context_manager.session.chat_id
        now = datetime.now(timezone.utc)

        if params.schedule_type == 'once':
            if not params.when:
                return "Error: 'when' is required for one-time tasks"
            parsed = dateparser.parse(params.when, settings={
                'PREFER_DATES_FROM': 'future',
                'RETURN_AS_TIMEZONE_AWARE': True,
            })
            if parsed is None:
                return f"Error: Could not parse date/time from '{params.when}'"
            if parsed <= now:
                return f"Error: Parsed time {parsed.isoformat()} is in the past (current time: {now.isoformat()})"
            run_at = parsed
            next_execution = run_at
            cron_expression = None
        elif params.schedule_type == 'recurring':
            if not params.cron_expression:
                return "Error: cron_expression is required for recurring tasks"
            try:
                next_execution = croniter(params.cron_expression, now).get_next(datetime)
            except (ValueError, KeyError) as e:
                return f"Error: Invalid cron expression: {e}"
            run_at = None
            cron_expression = params.cron_expression
        else:
            return f"Error: schedule_type must be 'once' or 'recurring', got '{params.schedule_type}'"

        record = await self.db.create_scheduled_task(
            chat_id=chat_id,
            user_id=self.user.id,
            title=params.title,
            prompt=params.prompt,
            schedule_type=params.schedule_type,
            run_at=run_at,
            cron_expression=cron_expression,
            next_execution=next_execution,
        )
        next_str = next_execution.strftime('%Y-%m-%d %H:%M UTC')
        return f"Scheduled task #{record['id']} '{params.title}' created. Next execution: {next_str}"

    @classmethod
    def get_description(cls) -> str:
        return "Schedule a task for later execution. Use 'once' with 'when' for one-time tasks, or 'recurring' with cron_expression for repeated tasks."

    @classmethod
    def get_system_prompt_addition(cls) -> Optional[str]:
        return (
            "You can schedule tasks using ScheduleTask. "
            "For one-time tasks, use schedule_type='once' and pass the user's time expression "
            "directly as the 'when' parameter — it supports natural language in any language "
            "(e.g. 'через 5 минут', 'следующий вторник в 10:00', 'завтра утром', "
            "'in 2 hours', 'next monday at 14:30'). The tool resolves the date automatically. "
            "For recurring tasks, use schedule_type='recurring' with a cron expression "
            "(e.g. '0 10 * * *' = daily at 10:00, '0 9 * * 1' = every Monday at 9:00, "
            "'30 14 * * 2' = every Tuesday at 14:30). "
            "The prompt field should contain a complete description of what to do when the task fires. "
            "Use ListScheduledTasks to see existing schedules and CancelScheduledTask to remove them."
        )


# --- ListScheduledTasks ---

class ListScheduledTasksParams(OpenAIFunctionParams):
    pass


class ListScheduledTasks(OpenAIFunction):
    PARAMS_SCHEMA = ListScheduledTasksParams

    async def run(self, params: ListScheduledTasksParams) -> Optional[str]:
        chat_id = self.context_manager.session.chat_id
        tasks = await self.db.get_scheduled_tasks(chat_id, enabled_only=True)
        if not tasks:
            return "No scheduled tasks."
        lines = ["Scheduled tasks:"]
        for t in tasks:
            schedule_info = t.get('cron_expression') or (t['run_at'].strftime('%Y-%m-%d %H:%M') if t.get('run_at') else '?')
            next_exec = t['next_execution'].strftime('%Y-%m-%d %H:%M') if t.get('next_execution') else '?'
            lines.append(
                f"  #{t['id']} [{t['schedule_type']}] {t['title']} "
                f"(schedule: {schedule_info}, next: {next_exec})"
            )
        return "\n".join(lines)

    @classmethod
    def get_description(cls) -> str:
        return "List all active scheduled tasks for this chat."


# --- CancelScheduledTask ---

class CancelScheduledTaskParams(OpenAIFunctionParams):
    task_id: int = Field(..., description="ID of the scheduled task to cancel")


class CancelScheduledTask(OpenAIFunction):
    PARAMS_SCHEMA = CancelScheduledTaskParams

    async def run(self, params: CancelScheduledTaskParams) -> Optional[str]:
        await self.db.disable_scheduled_task(params.task_id)
        return f"Scheduled task #{params.task_id} cancelled."

    @classmethod
    def get_description(cls) -> str:
        return "Cancel a scheduled task by its ID."


# All agent tool classes for registration
AGENT_TOOLS = [
    SpawnTask, WaitTask, CreatePlan, UpdatePlanStep, GetPlan, DeletePlan,
    ScheduleTask, ListScheduledTasks, CancelScheduledTask,
]
