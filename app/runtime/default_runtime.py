from typing import Callable, AsyncGenerator, Optional

import settings
from app.bot.chatgpt_manager import ChatGptManager
from app.context.context_manager import ContextManager, build_context_manager
from app.context.dialog_manager import DialogUtils
from app.llm_models import get_model_by_name
from app.openai_helpers.anthropic_chatgpt import AnthropicChatGPT
from app.openai_helpers.chatgpt import ChatGPT, parse_thinking
from app.runtime.conversation_session import ConversationSession
from app.runtime.events import (
    RuntimeEvent, StreamingContentDelta, FinalResponse,
    FunctionCallStarted, FunctionCallCompleted, ErrorEvent,
)
from app.runtime.context_utils import add_user_input_to_context
from app.runtime.side_effects import SideEffectHandler
from app.runtime.user_input import UserInput
from app.storage.db import DB, User


class DefaultLLMRuntime:
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

        # Add user input to context
        if user_input.has_content:
            await add_user_input_to_context(user_input, context_manager)

        llm_model = get_model_by_name(self.user.current_model)
        function_storage = None
        if llm_model.capabilities.tool_calling or llm_model.capabilities.function_calling:
            function_storage = await context_manager.get_function_storage()
        system_prompt = await context_manager.get_system_prompt()

        # HACK: TODO: refactor to factory
        if self.user.current_model == llm_model.ANTHROPIC_CLAUDE_35_SONNET:
            chat_gpt_manager = ChatGptManager(AnthropicChatGPT(llm_model, system_prompt, function_storage), self.db)
        else:
            chat_gpt_manager = ChatGptManager(ChatGPT(llm_model, system_prompt, function_storage), self.db)

        context_dialog_messages = await context_manager.get_context_messages()
        response_generator = await chat_gpt_manager.send_user_message(self.user, context_dialog_messages, is_cancelled)

        async for event in self._handle_response(
            chat_gpt_manager, context_manager, response_generator, function_storage, is_cancelled
        ):
            yield event

    async def _handle_response(
        self, chat_gpt_manager, context_manager, response_generator,
        function_storage, is_cancelled, recursive_count=0,
    ) -> AsyncGenerator[RuntimeEvent, None]:
        if recursive_count >= settings.SUCCESSIVE_FUNCTION_CALLS_LIMIT:
            raise ValueError('Model makes too many successive function calls')

        # Consume the streaming response and yield deltas
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

        # Strip thinking content before saving
        if dialog_message is not None:
            dialog_message = dialog_message.strip_thinking()

        has_content = bool(dialog_message.content)

        # Yield final response — adapter is responsible for saving content messages to context
        # (it needs the transport message_id for sub-dialogue tracking)
        yield FinalResponse(
            dialog_message=dialog_message,
            needs_context_save=has_content,
        )

        # Handle function calls (legacy)
        if dialog_message.function_call:
            if not has_content:
                # Content messages are saved by the adapter; only save function-call-only messages here
                await context_manager.add_message(dialog_message, -1)

            function_call = dialog_message.function_call
            async for event in self._run_function(function_call, function_storage, context_manager):
                if isinstance(event, FunctionCallCompleted):
                    yield event
                    if event.result is None:
                        return

                    function_response = DialogUtils.prepare_function_response(function_call.name, event.result)
                    context_dialog_messages = await context_manager.add_message(function_response, -1)
                    response_generator = await chat_gpt_manager.send_user_message(
                        self.user, context_dialog_messages, is_cancelled
                    )
                    async for sub_event in self._handle_response(
                        chat_gpt_manager, context_manager, response_generator,
                        function_storage, is_cancelled, recursive_count + 1,
                    ):
                        yield sub_event
                else:
                    yield event

        # Handle tool calls
        pass_tool_response_to_gpt = False
        if dialog_message.tool_calls:
            context_dialog_messages = None
            if not has_content:
                # Content messages are saved by the adapter; only save tool-call-only messages here
                await context_manager.add_message(dialog_message, -1)

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
                            tool_response = DialogUtils.prepare_tool_call_response(tool_call_id, event.result)
                            context_dialog_messages = await context_manager.add_message(tool_response, -1)
                    else:
                        yield event

            if pass_tool_response_to_gpt and context_dialog_messages:
                response_generator = await chat_gpt_manager.send_user_message(
                    self.user, context_dialog_messages, is_cancelled
                )
                async for event in self._handle_response(
                    chat_gpt_manager, context_manager, response_generator,
                    function_storage, is_cancelled, recursive_count + 1,
                ):
                    yield event

    async def _run_function(
        self, function_call, function_storage, context_manager, tool_call_id: str = None,
    ) -> AsyncGenerator[RuntimeEvent, None]:
        function_name = function_call.name
        function_args = function_call.arguments

        yield FunctionCallStarted(
            function_name=function_name,
            function_args=function_args,
            tool_call_id=tool_call_id,
        )

        try:
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
