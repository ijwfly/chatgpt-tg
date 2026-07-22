import contextvars
import logging
from dataclasses import dataclass
from typing import Optional, List

import pydantic
import pytz
from pydantic import Field

import settings
from app.functions.base import OpenAIFunction, OpenAIFunctionParams
from app.runtime.background_task_manager import BackgroundTaskManager
from app.runtime.plan_manager import PlanManager
from app.runtime.side_effects import SideEffectHandler

logger = logging.getLogger(__name__)


def get_user_timezone():
    """Resolve settings.USER_TIMEZONE to a tzinfo, falling back to UTC on invalid values."""
    try:
        return pytz.timezone(settings.USER_TIMEZONE)
    except pytz.UnknownTimeZoneError:
        logger.warning(f"Invalid USER_TIMEZONE '{settings.USER_TIMEZONE}', falling back to UTC")
        return pytz.utc


@dataclass
class AgentToolContext:
    bg_manager: BackgroundTaskManager
    plan_manager: PlanManager
    sub_agent_runner: object  # callable: async def(prompt) -> str


# Per-turn agent context. A ContextVar (not a class attribute) so that concurrent
# turns (e.g. a scheduled task firing while a user turn runs) don't clobber each
# other: tasks spawned within a turn inherit a copy of the turn's context.
agent_context_var: contextvars.ContextVar[Optional[AgentToolContext]] = contextvars.ContextVar(
    'agent_context', default=None,
)


class AgentFunction(OpenAIFunction):
    """Base class for agent tools. Accesses per-turn context via agent_context_var."""

    @property
    def agent_ctx(self) -> AgentToolContext:
        ctx = agent_context_var.get()
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
            "Use SpawnTask for parallelizable work. Results arrive via <background-results> messages."
        )

    @classmethod
    def get_status_message(cls) -> str:
        return 'Spawning sub-agent...'


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

    @classmethod
    def get_status_message(cls) -> str:
        return 'Waiting for background tasks...'


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
    def get_status_message(cls) -> str:
        return 'Creating plan...'


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

    @classmethod
    def get_status_message(cls) -> str:
        return 'Updating plan...'


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

    @classmethod
    def get_status_message(cls) -> str:
        return 'Reading plan...'


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

    @classmethod
    def get_status_message(cls) -> str:
        return 'Deleting plan...'


# --- ScheduleTask ---

class ScheduleTaskParams(OpenAIFunctionParams):
    title: str = Field(..., description="Short title for the scheduled task")
    prompt: str = Field(..., description="Natural language description of what to do when the task fires")
    schedule_type: str = Field(..., description="'once' for one-time or 'recurring' for repeated execution")
    when: Optional[str] = Field(None, description="For one-time tasks: natural language time in any language (e.g. 'через 5 минут', 'следующий вторник в 10:00', 'tomorrow at 9am', 'in 2 hours', 'next monday at 14:30'). Resolved automatically.")
    cron_expression: Optional[str] = Field(None, description="Cron expression for recurring tasks (e.g. '0 10 * * *' = daily at 10:00, '0 9 * * 1' = every Monday at 9:00, '30 14 * * 2' = every Tuesday at 14:30)")


class ScheduleTask(OpenAIFunction):
    PARAMS_SCHEMA = ScheduleTaskParams

    def _snapshot_context_message_ids(self) -> list:
        """Snapshot the current dialog branch so the task fires with the conversation it was born in.

        Trailing messages of an unfinished tool exchange (including the ScheduleTask call itself)
        are trimmed — the stored branch must end on a plain user/assistant message to be a valid
        LLM context on its own.
        """
        dialog_manager = self.context_manager.dialog_manager
        if dialog_manager is None or not dialog_manager.messages:
            return []
        messages = list(dialog_manager.messages)
        while messages and (
            messages[-1].message.role in ('tool', 'function')
            or messages[-1].message.tool_calls
            or messages[-1].message.function_call
        ):
            messages.pop()
        return [m.id for m in messages]

    async def run(self, params: ScheduleTaskParams) -> Optional[str]:
        from datetime import datetime, timezone
        from croniter import croniter
        import dateparser

        chat_id = self.context_manager.session.chat_id
        now = datetime.now(timezone.utc)
        user_tz = get_user_timezone()
        local_now = datetime.now(user_tz)

        if params.schedule_type == 'once':
            if not params.when:
                return "Error: 'when' is required for one-time tasks"
            parsed = dateparser.parse(params.when, settings={
                'PREFER_DATES_FROM': 'future',
                'RETURN_AS_TIMEZONE_AWARE': True,
                'TIMEZONE': str(user_tz),
                'TO_TIMEZONE': 'UTC',
                'RELATIVE_BASE': local_now.replace(tzinfo=None),
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
                next_execution = croniter(params.cron_expression, local_now).get_next(datetime)
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
            context_message_ids=self._snapshot_context_message_ids(),
        )
        next_str = next_execution.astimezone(user_tz).strftime('%Y-%m-%d %H:%M') + f' ({user_tz})'
        return f"Scheduled task #{record['id']} '{params.title}' created. Next execution: {next_str}"

    @classmethod
    def get_description(cls) -> str:
        return "Schedule a task for later execution. Use 'once' with 'when' for one-time tasks, or 'recurring' with cron_expression for repeated tasks."

    @classmethod
    def get_status_message(cls) -> str:
        return 'Scheduling task...'

    @classmethod
    def get_system_prompt_addition(cls) -> Optional[str]:
        return (
            "Use ScheduleTask for deferred execution. For one-time tasks use schedule_type='once' "
            "with natural language 'when'. For recurring use schedule_type='recurring' with cron_expression. "
            f"Times in 'when' and cron expressions are interpreted in the bot's timezone ({settings.USER_TIMEZONE}): "
            "pass the user's times as-is, do not convert them to UTC. "
            "Use ListScheduledTasks/CancelScheduledTask to manage. "
            "When you receive a <scheduled_task_execution> message, a previously scheduled task has fired: "
            "execute its instructions immediately — never call ScheduleTask to re-schedule it "
            "(recurring tasks re-schedule themselves automatically)."
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
        user_tz = get_user_timezone()
        lines = [f"Scheduled tasks (times in {user_tz}):"]
        for t in tasks:
            schedule_info = t.get('cron_expression') or (t['run_at'].astimezone(user_tz).strftime('%Y-%m-%d %H:%M') if t.get('run_at') else '?')
            next_exec = t['next_execution'].astimezone(user_tz).strftime('%Y-%m-%d %H:%M') if t.get('next_execution') else '?'
            lines.append(
                f"  #{t['id']} [{t['schedule_type']}] {t['title']} "
                f"(schedule: {schedule_info}, next: {next_exec})"
            )
        return "\n".join(lines)

    @classmethod
    def get_description(cls) -> str:
        return "List all active scheduled tasks for this chat."

    @classmethod
    def get_status_message(cls) -> str:
        return 'Listing scheduled tasks...'


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

    @classmethod
    def get_status_message(cls) -> str:
        return 'Cancelling scheduled task...'


# Core agent tools (always registered)
AGENT_TOOLS_CORE = [
    SpawnTask, WaitTask,
    ScheduleTask, ListScheduledTasks, CancelScheduledTask,
]

# Plan tools: only one set is active at a time depending on plan state
PLAN_TOOLS_NO_PLAN = [CreatePlan]
PLAN_TOOLS_WITH_PLAN = [UpdatePlanStep, GetPlan, DeletePlan]

# Sub-agent gets only these plan tools (no create/delete)
SUB_AGENT_EXCLUDED_TOOLS = frozenset({
    'SpawnTask', 'WaitTask', 'CreatePlan', 'DeletePlan',
    'ScheduleTask', 'ListScheduledTasks', 'CancelScheduledTask',
})
