from typing import Callable, AsyncGenerator, Optional, List

import asyncio
import logging
import time

import settings
from app.bot.chatgpt_manager import ChatGptManager
from app.context.context_manager import ContextManager, build_context_manager
from app.context.dialog_manager import DialogUtils
from app.functions.agent_tools import (
    AgentToolContext, agent_context_var,
    AGENT_TOOLS_CORE, PLAN_TOOLS_NO_PLAN, PLAN_TOOLS_WITH_PLAN,
    SUB_AGENT_EXCLUDED_TOOLS,
)
from app.functions.bash_sandbox import SANDBOX_TOOLS
from app.functions.web_agents import WEB_AGENT_TOOLS
from app.functions.mcp.mcp_function_storage import MCPFunctionManager
from app.llm_models import get_model_by_name
from app.openai_helpers.anthropic_chatgpt import AnthropicChatGPT
from app.openai_helpers.chatgpt import ChatGPT, DialogMessage, parse_thinking
from app.openai_helpers.function_storage import FunctionStorage
from app.runtime.background_task_manager import BackgroundTaskManager
from app.runtime.context_utils import add_user_input_to_context
from app.runtime.conversation_session import ConversationSession
from app.runtime.events import (
    RuntimeEvent, StreamingContentDelta, FinalResponse,
    FunctionCallStarted, FunctionCallCompleted, ErrorEvent,
)
from app.runtime.langfuse_utils import build_langfuse_metadata
from app.runtime.plan_manager import PlanManager
from app.runtime.side_effects import SideEffectHandler
from app.runtime.user_input import UserInput
from app.storage.db import DB, User
from app.storage.user_role import check_access_conditions

logger = logging.getLogger(__name__)

PLAN_TOOL_NAMES = frozenset({"CreatePlan", "UpdatePlanStep", "GetPlan", "DeletePlan"})


def _sync_plan_tools(function_storage: FunctionStorage, plan_exists: bool):
    """Register the right set of plan tools based on whether a plan exists."""
    for name in PLAN_TOOL_NAMES:
        function_storage.functions.pop(name, None)
    tools = PLAN_TOOLS_WITH_PLAN if plan_exists else PLAN_TOOLS_NO_PLAN
    for tool_cls in tools:
        function_storage.register(tool_cls)


def _has_plan_tool_call(dialog_message: DialogMessage) -> bool:
    if dialog_message.function_call and dialog_message.function_call.name in PLAN_TOOL_NAMES:
        return True
    if dialog_message.tool_calls:
        for tc in dialog_message.tool_calls:
            if tc.function.name in PLAN_TOOL_NAMES:
                return True
    return False


class AgentRuntime:
    def __init__(self, db: DB, user: User, side_effects: SideEffectHandler,
                 context_manager: Optional[ContextManager] = None):
        self.db = db
        self.user = user
        self.side_effects = side_effects
        self._context_manager = context_manager

    async def process_turn(
        self,
        user_input: UserInput,
        session: ConversationSession,
        is_cancelled: Callable[[], bool],
    ) -> AsyncGenerator[RuntimeEvent, None]:
        context_manager = self._context_manager
        if context_manager is None:
            context_manager = await build_context_manager(self.db, self.user, session)

        if user_input.has_content:
            await add_user_input_to_context(user_input, context_manager)

        llm_model = get_model_by_name(self.user.current_model)
        function_storage = None
        if llm_model.capabilities.tool_calling or llm_model.capabilities.function_calling:
            function_storage = await context_manager.get_function_storage()
        if function_storage is None:
            function_storage = FunctionStorage()

        # Load agent-specific MCP tools (MCP_SERVERS_AGENT)
        for mcp_config in settings.MCP_SERVERS_AGENT:
            if check_access_conditions(mcp_config.min_role, self.user.role):
                mcp_manager = MCPFunctionManager(mcp_config.url, mcp_config.headers)
                try:
                    mcp_tools = await mcp_manager.get_tools()
                    for tool in mcp_tools:
                        function_storage.register(tool)
                except Exception as e:
                    logger.error(f"Error loading agent MCP tools from {mcp_config.url}: {e}")

        # Register core agent tools
        for tool_cls in AGENT_TOOLS_CORE:
            function_storage.register(tool_cls)

        # Register bash sandbox tools
        if settings.ENABLE_BASH_SANDBOX:
            for tool_cls in SANDBOX_TOOLS:
                function_storage.register(tool_cls)

        # Register web agent tools
        if settings.ENABLE_WEB_AGENTS:
            for tool_cls in WEB_AGENT_TOOLS:
                function_storage.register(tool_cls)

        # Create per-turn managers and load plan state
        bg_manager = BackgroundTaskManager(timeout=settings.AGENT_BG_TASK_TIMEOUT)
        plan_manager = PlanManager(self.db, session.chat_id, side_effects=self.side_effects)
        await plan_manager.load()

        # Register plan tools based on current plan state (before building system prompt)
        _sync_plan_tools(function_storage, plan_manager._plan is not None)

        system_prompt = await context_manager.get_system_prompt()
        if settings.AGENT_SYSTEM_PROMPT:
            system_prompt = settings.AGENT_SYSTEM_PROMPT + '\n\n' + system_prompt

        # Create LLM client (same pattern as DefaultLLMRuntime)
        langfuse_metadata = build_langfuse_metadata(self.user)
        if self.user.current_model == llm_model.ANTHROPIC_CLAUDE_35_SONNET:
            chat_gpt = AnthropicChatGPT(llm_model, system_prompt, function_storage, langfuse_metadata=langfuse_metadata)
        else:
            chat_gpt = ChatGPT(llm_model, system_prompt, function_storage, langfuse_metadata=langfuse_metadata)
        chat_gpt_manager = ChatGptManager(chat_gpt, self.db)

        # Build sub-agent runner
        async def sub_agent_runner(prompt: str) -> str:
            deadline = time.monotonic() + settings.AGENT_BG_TASK_TIMEOUT
            return await self._run_sub_agent(
                prompt, llm_model, function_storage, context_manager, deadline,
            )

        # Set agent context for tools (ContextVar: isolated per turn, inherited by spawned tasks)
        agent_ctx = AgentToolContext(
            bg_manager=bg_manager,
            plan_manager=plan_manager,
            sub_agent_runner=sub_agent_runner,
        )
        ctx_token = agent_context_var.set(agent_ctx)

        try:
            async for event in self._agent_loop(
                chat_gpt, chat_gpt_manager, context_manager, function_storage,
                bg_manager, plan_manager, is_cancelled,
            ):
                yield event
        finally:
            await bg_manager.cancel_all()
            agent_context_var.reset(ctx_token)

    async def _agent_loop(
        self, chat_gpt, chat_gpt_manager: ChatGptManager, context_manager: ContextManager,
        function_storage: FunctionStorage, bg_manager: BackgroundTaskManager,
        plan_manager: PlanManager,
        is_cancelled: Callable[[], bool],
    ) -> AsyncGenerator[RuntimeEvent, None]:
        iteration = 0
        iterations_since_plan_tool = 0
        plan_exists = plan_manager._plan is not None
        while iteration < settings.AGENT_MAX_ITERATIONS:
            if is_cancelled():
                return

            # A) Drain background notifications and inject into context
            notifications = bg_manager.drain_notifications()
            if notifications:
                notif_text = "\n".join(
                    f"[task:{n.task_id}] {n.status}: {n.result}" for n in notifications
                )
                user_msg = DialogUtils.prepare_user_message(
                    f"<background-results>\n{notif_text}\n</background-results>"
                )
                await context_manager.add_message(user_msg, -1)
                ack_msg = DialogMessage(role="assistant", content="Acknowledged background results.")
                await context_manager.add_message(ack_msg, -1)

            # B) Inject plan reminder into context if needed
            should_inject_plan = False
            if iteration == 0 and plan_exists:
                should_inject_plan = True
            elif plan_exists and iterations_since_plan_tool >= settings.AGENT_PLAN_REMINDER_INTERVAL:
                should_inject_plan = True

            if should_inject_plan:
                plan_text = await plan_manager.get_plan()
                if plan_text and plan_text != "No active plan.":
                    user_msg = DialogUtils.prepare_user_message(
                        f"<plan-reminder>\n{plan_text}\n</plan-reminder>"
                    )
                    await context_manager.add_message(user_msg, -1)
                    ack_msg = DialogMessage(role="assistant", content="Acknowledged current plan state.")
                    await context_manager.add_message(ack_msg, -1)
                    iterations_since_plan_tool = 0

            # C) Sync plan tools based on current state
            _sync_plan_tools(function_storage, plan_exists)

            # D) Get context and call LLM
            context_dialog_messages = await context_manager.get_context_messages()
            response_generator = await chat_gpt_manager.send_user_message(
                self.user, context_dialog_messages, is_cancelled
            )

            # E) Consume streaming response and yield deltas
            dialog_message = None
            first_iteration = True
            async for dialog_message in response_generator:
                if first_iteration:
                    first_iteration = False
                    continue

                if dialog_message.function_call is not None or dialog_message.tool_calls is not None:
                    continue

                if isinstance(dialog_message.content, str):
                    visible, thinking, is_thinking = parse_thinking(dialog_message.content)
                else:
                    visible, thinking, is_thinking = '', '', False

                yield StreamingContentDelta(
                    visible_text=visible,
                    thinking_text=thinking,
                    is_thinking=is_thinking,
                )

            if dialog_message is not None:
                dialog_message = dialog_message.strip_thinking()

            has_content = bool(dialog_message.content)
            has_tool_calls = bool(dialog_message.tool_calls or dialog_message.function_call)
            yield FinalResponse(
                dialog_message=dialog_message,
                needs_context_save=has_content and not has_tool_calls,
            )

            # F) If no tool calls — check for pending bg tasks
            if not dialog_message.tool_calls and not dialog_message.function_call:
                if bg_manager.has_pending():
                    await bg_manager.wait_pending(timeout=settings.AGENT_BG_TASK_TIMEOUT)
                new_notifs = bg_manager.drain_notifications()
                if new_notifs:
                    # Put them back for the next iteration to inject
                    bg_manager.requeue(new_notifs)
                    iteration += 1
                    continue
                break

            # G) Execute tool calls (iterative, not recursive)
            if dialog_message.function_call:
                await context_manager.add_message(dialog_message, -1)

                function_call = dialog_message.function_call
                async for event in self._run_function(function_call, function_storage, context_manager):
                    if isinstance(event, FunctionCallCompleted):
                        yield event
                        if event.result is None:
                            return
                        function_response = DialogUtils.prepare_function_response(
                            function_call.name, event.result
                        )
                        await context_manager.add_message(function_response, -1)
                    else:
                        yield event

            elif dialog_message.tool_calls:
                await context_manager.add_message(dialog_message, -1)

                pass_tool_response_to_gpt = False
                for tool_call in dialog_message.tool_calls:
                    if tool_call.type != 'function':
                        raise ValueError(f'Unknown tool call type: {tool_call.type}')
                    tool_call_id = tool_call.id
                    function_call = tool_call.function

                    async for event in self._run_function(
                        function_call, function_storage, context_manager, tool_call_id
                    ):
                        if isinstance(event, FunctionCallCompleted):
                            yield event
                            if event.result is not None:
                                pass_tool_response_to_gpt = True
                                tool_response = DialogUtils.prepare_tool_call_response(
                                    tool_call_id, event.result
                                )
                                await context_manager.add_message(tool_response, -1)
                        else:
                            yield event

                if not pass_tool_response_to_gpt:
                    break

            # H) Update plan tracking counters
            if _has_plan_tool_call(dialog_message):
                iterations_since_plan_tool = 0
                plan_exists = plan_manager._plan is not None
            else:
                iterations_since_plan_tool += 1

            iteration += 1

    async def _run_function(
        self, function_call, function_storage: FunctionStorage,
        context_manager: ContextManager, tool_call_id: str = None,
    ) -> AsyncGenerator[RuntimeEvent, None]:
        function_name = function_call.name
        function_args = function_call.arguments

        function_class = None
        status_message = f'Running {function_name}...'
        try:
            function_class = function_storage.get_function_class(function_name)
            status_message = function_class.get_status_message()
        except Exception:
            pass

        yield FunctionCallStarted(
            function_name=function_name,
            function_args=function_args,
            tool_call_id=tool_call_id,
            status_message=status_message,
        )

        try:
            if function_class is None:
                function_class = function_storage.get_function_class(function_name)
            function = function_class(self.user, self.db, context_manager, self.side_effects, tool_call_id)
            function_response_raw = await function.run_str_args(function_args)
        except Exception as e:
            function_response_raw = f"Error: {e}"

        yield FunctionCallCompleted(
            function_name=function_name,
            function_args=function_args,
            result=function_response_raw,
            tool_call_id=tool_call_id,
        )

    async def _run_sub_agent(
        self, prompt: str, llm_model, parent_function_storage: FunctionStorage,
        parent_context_manager: ContextManager, deadline: float,
    ) -> str:
        """Run a sub-agent loop with limited tools, plan context and parent conversation context.

        Uses the same agent context (ContextVar) as the parent. Excluded tools prevent
        recursive spawning and plan creation/deletion by sub-agents.

        `deadline` is a time.monotonic() timestamp. The sub-agent stops itself when the
        deadline approaches and returns whatever progress it has made, instead of being
        cancelled from outside and losing everything.
        """
        started_at = time.monotonic()

        # Build a function_storage for the sub-agent: exclude management tools
        sub_function_storage = FunctionStorage()
        for func_name, func_data in parent_function_storage.functions.items():
            if func_name not in SUB_AGENT_EXCLUDED_TOOLS:
                sub_function_storage.functions[func_name] = func_data

        # Build system prompt with optional plan context
        sub_system_prompt = "You are a sub-agent working on a specific task. Complete it and return your result."
        agent_ctx = agent_context_var.get()
        if agent_ctx is not None:
            plan_text = await agent_ctx.plan_manager.get_plan()
            if plan_text and plan_text != "No active plan.":
                sub_system_prompt += f"\n\nCurrent plan:\n{plan_text}"

        langfuse_metadata = build_langfuse_metadata(self.user)
        if llm_model.ANTHROPIC_CLAUDE_35_SONNET == self.user.current_model:
            sub_chatgpt = AnthropicChatGPT(llm_model, sub_system_prompt, sub_function_storage, langfuse_metadata=langfuse_metadata)
        else:
            sub_chatgpt = ChatGPT(llm_model, sub_system_prompt, sub_function_storage, langfuse_metadata=langfuse_metadata)

        # Snapshot the parent conversation so the sub-agent starts with full context
        # instead of cold. Trailing messages of an unfinished tool exchange (including
        # the SpawnTask call itself) are trimmed to keep the context valid.
        context_messages = list(await parent_context_manager.get_context_messages())
        while context_messages and (
            context_messages[-1].role in ('tool', 'function')
            or context_messages[-1].tool_calls
            or context_messages[-1].function_call
        ):
            context_messages.pop()
        messages = context_messages + [DialogUtils.prepare_user_message(prompt)]

        executed_tools: List[str] = []
        last_assistant_text = ''

        def partial_result(reason: str) -> str:
            lines = [f"Sub-agent stopped early: {reason}. Progress so far:"]
            if executed_tools:
                lines.append("Tool calls executed:")
                lines.extend(f"- {entry}" for entry in executed_tools)
            if last_assistant_text:
                lines.append(f"Last assistant output:\n{last_assistant_text}")
            if not executed_tools and not last_assistant_text:
                lines.append("(no progress)")
            return "\n".join(lines)

        async def run_tool(function_name: str, arguments: str, tool_call_id) -> str:
            tool_started = time.monotonic()
            try:
                function_class = sub_function_storage.get_function_class(function_name)
                function = function_class(
                    self.user, self.db, parent_context_manager,
                    self.side_effects, tool_call_id
                )
                result = await function.run_str_args(arguments)
            except Exception as e:
                result = f"Error: {e}"
            result = result if result is not None else "(no output)"
            logger.debug(f"[sub-agent] tool {function_name} finished in {time.monotonic() - tool_started:.1f}s")
            executed_tools.append(f"{function_name}: {result[:200]}")
            return result

        for iteration in range(settings.AGENT_SUB_AGENT_MAX_ITERATIONS):
            remaining = deadline - time.monotonic()
            if remaining < 30:
                logger.warning(f"[sub-agent] deadline reached at iteration {iteration}, returning partial result")
                return partial_result(f"deadline reached after {time.monotonic() - started_at:.0f}s")

            llm_timeout = min(settings.AGENT_SUB_AGENT_LLM_TIMEOUT, remaining)
            llm_started = time.monotonic()
            try:
                dialog_message, _ = await asyncio.wait_for(
                    sub_chatgpt.send_messages(messages), timeout=llm_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(f"[sub-agent] LLM call timed out after {llm_timeout:.0f}s at iteration {iteration}")
                return partial_result(f"LLM call timed out after {llm_timeout:.0f}s")
            logger.debug(f"[sub-agent] iteration {iteration}: LLM call took {time.monotonic() - llm_started:.1f}s")
            dialog_message = dialog_message.strip_thinking()

            text_content = dialog_message.get_text_content()
            if text_content:
                last_assistant_text = text_content

            # If no tool calls, we're done
            if not dialog_message.tool_calls and not dialog_message.function_call:
                logger.info(f"[sub-agent] completed in {time.monotonic() - started_at:.1f}s, {iteration + 1} iteration(s)")
                return text_content or "(empty response)"

            # Handle tool calls
            messages.append(dialog_message)

            if dialog_message.tool_calls:
                for tool_call in dialog_message.tool_calls:
                    if tool_call.type != 'function':
                        continue
                    result = await run_tool(tool_call.function.name, tool_call.function.arguments, tool_call.id)
                    messages.append(DialogUtils.prepare_tool_call_response(tool_call.id, result))

            elif dialog_message.function_call:
                fc = dialog_message.function_call
                result = await run_tool(fc.name, fc.arguments, None)
                messages.append(DialogUtils.prepare_function_response(fc.name, result))

        logger.warning(f"[sub-agent] iteration limit reached after {time.monotonic() - started_at:.1f}s")
        return partial_result("iteration limit reached")
