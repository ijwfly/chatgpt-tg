from typing import Callable, AsyncGenerator, Optional, List

import logging

import settings
from app.bot.chatgpt_manager import ChatGptManager
from app.context.context_manager import ContextManager, build_context_manager
from app.context.dialog_manager import DialogUtils
from app.functions.agent_tools import (
    AgentFunction, AgentToolContext, AGENT_TOOLS,
)
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
from app.runtime.plan_manager import PlanManager
from app.runtime.side_effects import SideEffectHandler
from app.runtime.user_input import UserInput
from app.storage.db import DB, User
from app.storage.user_role import check_access_conditions

logger = logging.getLogger(__name__)

PLAN_TOOL_NAMES = frozenset({"CreatePlan", "UpdatePlanStep", "GetPlan", "DeletePlan"})


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

        # Register agent tools BEFORE building system prompt so their
        # get_system_prompt_addition() is included
        for tool_cls in AGENT_TOOLS:
            function_storage.register(tool_cls)

        system_prompt = await context_manager.get_system_prompt()
        if settings.AGENT_SYSTEM_PROMPT:
            system_prompt = settings.AGENT_SYSTEM_PROMPT + '\n\n' + system_prompt

        # Create per-turn managers
        bg_manager = BackgroundTaskManager(timeout=settings.AGENT_BG_TASK_TIMEOUT)
        plan_manager = PlanManager(self.db, session.chat_id, side_effects=self.side_effects)
        await plan_manager.load()

        # Create LLM client (same pattern as DefaultLLMRuntime)
        if self.user.current_model == llm_model.ANTHROPIC_CLAUDE_35_SONNET:
            chat_gpt = AnthropicChatGPT(llm_model, system_prompt, function_storage)
        else:
            chat_gpt = ChatGPT(llm_model, system_prompt, function_storage)
        chat_gpt_manager = ChatGptManager(chat_gpt, self.db)

        # Build sub-agent runner
        async def sub_agent_runner(prompt: str) -> str:
            return await self._run_sub_agent(
                prompt, llm_model, function_storage, context_manager,
            )

        # Set agent context on tool classes
        agent_ctx = AgentToolContext(
            bg_manager=bg_manager,
            plan_manager=plan_manager,
            sub_agent_runner=sub_agent_runner,
        )
        AgentFunction._agent_context = agent_ctx

        try:
            async for event in self._agent_loop(
                chat_gpt, chat_gpt_manager, context_manager, function_storage,
                bg_manager, plan_manager, is_cancelled,
            ):
                yield event
        finally:
            await bg_manager.cancel_all()
            AgentFunction._agent_context = None

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

            # C) Get context and call LLM
            context_dialog_messages = await context_manager.get_context_messages()
            response_generator = await chat_gpt_manager.send_user_message(
                self.user, context_dialog_messages, is_cancelled
            )

            # C) Consume streaming response and yield deltas
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
            yield FinalResponse(
                dialog_message=dialog_message,
                needs_context_save=has_content,
            )

            # D) If no tool calls — check for pending bg tasks
            if not dialog_message.tool_calls and not dialog_message.function_call:
                if bg_manager.has_pending():
                    await bg_manager.wait_pending(timeout=30)
                    new_notifs = bg_manager.drain_notifications()
                    if new_notifs:
                        # Put them back for the next iteration to inject
                        for n in new_notifs:
                            await bg_manager._notification_queue.put(n)
                        iteration += 1
                        continue
                break

            # E) Execute tool calls (iterative, not recursive)
            if dialog_message.function_call:
                if not has_content:
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
                if not has_content:
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

            # F) Update plan tracking counters
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

        yield FunctionCallStarted(
            function_name=function_name,
            function_args=function_args,
            tool_call_id=tool_call_id,
        )

        function_class = function_storage.get_function_class(function_name)
        function = function_class(self.user, self.db, context_manager, self.side_effects, tool_call_id)
        function_response_raw = await function.run_str_args(function_args)

        yield FunctionCallCompleted(
            function_name=function_name,
            function_args=function_args,
            result=function_response_raw,
            tool_call_id=tool_call_id,
        )

    async def _run_sub_agent(
        self, prompt: str, llm_model, parent_function_storage: FunctionStorage,
        parent_context_manager: ContextManager,
    ) -> str:
        """Run a sub-agent loop with tools but without SpawnTask (no nesting).

        Uses the same _agent_context as the parent — SpawnTask is excluded from
        the sub-agent's function_storage, which prevents recursive spawning
        without touching the shared class variable.
        """
        # Build a function_storage for the sub-agent: same tools minus SpawnTask
        sub_function_storage = FunctionStorage()
        for func_name, func_data in parent_function_storage.functions.items():
            if func_name != 'SpawnTask':
                sub_function_storage.functions[func_name] = func_data

        sub_system_prompt = (
            f"You are a sub-agent working on a specific task. Complete it and return your result.\n\n"
            f"Task: {prompt}"
        )

        if llm_model.ANTHROPIC_CLAUDE_35_SONNET == self.user.current_model:
            sub_chatgpt = AnthropicChatGPT(llm_model, sub_system_prompt, sub_function_storage)
        else:
            sub_chatgpt = ChatGPT(llm_model, sub_system_prompt, sub_function_storage)

        # No context swapping — sub-agent shares parent's _agent_context.
        # SpawnTask is not in sub_function_storage, so nesting is impossible.
        messages = [DialogUtils.prepare_user_message(prompt)]
        for _ in range(settings.AGENT_SUB_AGENT_MAX_ITERATIONS):
            dialog_message, _ = await sub_chatgpt.send_messages(messages)
            dialog_message = dialog_message.strip_thinking()

            # If no tool calls, we're done
            if not dialog_message.tool_calls and not dialog_message.function_call:
                return dialog_message.get_text_content() or "(empty response)"

            # Handle tool calls
            messages.append(dialog_message)

            if dialog_message.tool_calls:
                for tool_call in dialog_message.tool_calls:
                    if tool_call.type != 'function':
                        continue
                    function_class = sub_function_storage.get_function_class(tool_call.function.name)
                    function = function_class(
                        self.user, self.db, parent_context_manager,
                        self.side_effects, tool_call.id
                    )
                    result = await function.run_str_args(tool_call.function.arguments)
                    result = result if result is not None else "(no output)"
                    messages.append(DialogUtils.prepare_tool_call_response(tool_call.id, result))

            elif dialog_message.function_call:
                fc = dialog_message.function_call
                function_class = sub_function_storage.get_function_class(fc.name)
                function = function_class(
                    self.user, self.db, parent_context_manager,
                    self.side_effects, None
                )
                result = await function.run_str_args(fc.arguments)
                result = result if result is not None else "(no output)"
                messages.append(DialogUtils.prepare_function_response(fc.name, result))

        return "Sub-agent reached iteration limit without completing"
