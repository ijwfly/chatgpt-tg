"""Isolated sub-agent loop for web agent tools (web_search_agent / web_scraper_agent).

Unlike AgentRuntime._run_sub_agent, this runner starts with a clean context
(only the task message), uses its own small tool set and records LLM usage
for billing. It does not depend on the agent ContextVar, so the web agent
tools work from both DefaultLLMRuntime and AgentRuntime.
"""

from typing import List

import asyncio
import logging
import time

import settings
from app.context.dialog_manager import DialogUtils
from app.llm_models import get_model_by_name
from app.openai_helpers.anthropic_chatgpt import AnthropicChatGPT
from app.openai_helpers.chatgpt import ChatGPT
from app.openai_helpers.llm_client import AnthropicAsyncClient
from app.openai_helpers.function_storage import FunctionStorage
from app.openai_helpers.utils import calculate_completion_usage_price
from app.runtime.langfuse_utils import build_langfuse_metadata

logger = logging.getLogger(__name__)


async def run_web_agent(
    user, db, context_manager, side_effects, *,
    system_prompt: str,
    task: str,
    tool_classes: List[type],
) -> str:
    model_name = settings.WEB_AGENT_MODEL or user.current_model
    try:
        llm_model = get_model_by_name(model_name)
    except ValueError:
        logger.warning(f"[web-agent] model {model_name!r} not available, falling back to user model")
        model_name = user.current_model
        llm_model = get_model_by_name(model_name)

    function_storage = FunctionStorage()
    for tool_cls in tool_classes:
        function_storage.register(tool_cls)

    langfuse_metadata = build_langfuse_metadata(user)
    if issubclass(llm_model.api_client, AnthropicAsyncClient):
        chatgpt = AnthropicChatGPT(llm_model, system_prompt, function_storage, langfuse_metadata=langfuse_metadata)
    else:
        chatgpt = ChatGPT(llm_model, system_prompt, function_storage, langfuse_metadata=langfuse_metadata)

    messages = [DialogUtils.prepare_user_message(task)]
    started_at = time.monotonic()
    last_assistant_text = ''

    def partial_result(reason: str) -> str:
        result = f"Web agent stopped early: {reason}."
        if last_assistant_text:
            result += f" Partial findings:\n{last_assistant_text}"
        return result

    async def run_tool(function_name: str, arguments: str, tool_call_id) -> str:
        try:
            function_class = function_storage.get_function_class(function_name)
            function = function_class(user, db, context_manager, side_effects, tool_call_id)
            result = await function.run_str_args(arguments)
        except Exception as e:
            result = f"Error: {e}"
        return result if result is not None else "(no output)"

    # one extra iteration for finalization: when the tool call budget is exhausted,
    # the model gets a last chance to answer from what it has instead of returning a partial result
    max_iterations = settings.WEB_AGENT_MAX_ITERATIONS
    for iteration in range(max_iterations + 1):
        is_finalization = iteration == max_iterations
        if is_finalization:
            messages.append(DialogUtils.prepare_user_message(
                "You have reached the tool call limit. Provide your final answer now "
                "based on what you already have. Do not call any more tools."
            ))
        try:
            dialog_message, usage = await asyncio.wait_for(
                chatgpt.send_messages(messages), timeout=settings.WEB_AGENT_LLM_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[web-agent] LLM call timed out at iteration {iteration}")
            return partial_result(f"LLM call timed out after {settings.WEB_AGENT_LLM_TIMEOUT}s")
        except Exception as e:
            logger.exception("[web-agent] LLM call failed")
            return partial_result(f"LLM call failed: {e}")

        price = calculate_completion_usage_price(usage.prompt_tokens, usage.completion_tokens, usage.model)
        await db.create_completion_usage(
            user.id, usage.prompt_tokens, usage.completion_tokens, usage.total_tokens, usage.model, price,
        )

        dialog_message = dialog_message.strip_thinking()
        text_content = dialog_message.get_text_content() if dialog_message.content is not None else ''
        if text_content:
            last_assistant_text = text_content

        if not dialog_message.tool_calls and not dialog_message.function_call:
            logger.info(f"[web-agent] completed in {time.monotonic() - started_at:.1f}s, {iteration + 1} iteration(s)")
            return text_content or "(empty response)"

        if is_finalization:
            break

        messages.append(dialog_message)

        if dialog_message.tool_calls:
            function_calls = [tc for tc in dialog_message.tool_calls if tc.type == 'function']
            results = await asyncio.gather(*[
                run_tool(tc.function.name, tc.function.arguments, tc.id) for tc in function_calls
            ])
            for tool_call, result in zip(function_calls, results):
                messages.append(DialogUtils.prepare_tool_call_response(tool_call.id, result))
        elif dialog_message.function_call:
            fc = dialog_message.function_call
            result = await run_tool(fc.name, fc.arguments, None)
            messages.append(DialogUtils.prepare_function_response(fc.name, result))

    logger.warning(f"[web-agent] iteration limit reached after {time.monotonic() - started_at:.1f}s")
    return partial_result("iteration limit reached")
