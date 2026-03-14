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
        return "Spawn a background sub-agent that runs autonomously with its own tool access. Returns task_id immediately. Use check_task to monitor progress, or results will be delivered automatically."

    @classmethod
    def get_system_prompt_addition(cls) -> Optional[str]:
        return (
            "You can spawn background sub-agents using SpawnTask. Each sub-agent gets its own LLM call loop "
            "with tool access and works independently. Use this for parallelizable work — research, analysis, "
            "or any task that can run concurrently. Results are automatically delivered to you via "
            "<background-results> messages. When you receive background results, you MUST update the "
            "corresponding plan steps using UpdatePlanStep before responding to the user."
        )


# --- CheckTask ---

class CheckTaskParams(OpenAIFunctionParams):
    task_id: Optional[str] = Field(None, description="Task ID to check, or omit to list all tasks")


class CheckTask(AgentFunction):
    PARAMS_SCHEMA = CheckTaskParams

    async def run(self, params: CheckTaskParams) -> Optional[str]:
        return self.agent_ctx.bg_manager.check(params.task_id)

    @classmethod
    def get_description(cls) -> str:
        return "Check the status of background tasks. Provide task_id to check a specific task, or omit to list all."


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


# All agent tool classes for registration
AGENT_TOOLS = [SpawnTask, CheckTask, CreatePlan, UpdatePlanStep, GetPlan, DeletePlan]
