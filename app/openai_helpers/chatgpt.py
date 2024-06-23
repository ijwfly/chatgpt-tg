import json
from contextlib import suppress
from typing import List, Any, Optional, Callable, Union

import settings
from app.bot.utils import merge_dicts
from app.openai_helpers.count_tokens import count_messages_tokens, count_tokens_from_functions, count_string_tokens
from app.openai_helpers.function_storage import FunctionStorage

import pydantic

from app.openai_helpers.llm_client_factory import LLMClientFactory


class FunctionCall(pydantic.BaseModel):
    name: Optional[str]
    arguments: Optional[str]


class ToolCall(pydantic.BaseModel):
    id: str
    type: str
    function: FunctionCall


class CompletionUsage(pydantic.BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    model: str


class DialogMessageImageUrl(pydantic.BaseModel):
    url: str


class DialogMessageContentPart(pydantic.BaseModel):
    type: str
    text: Optional[str] = None
    image_url: Optional[DialogMessageImageUrl] = None


class DialogMessage(pydantic.BaseModel):
    role: Optional[str] = None
    name: Optional[str] = None
    content: Union[Optional[str], Optional[List[DialogMessageContentPart]]] = None
    function_call: Optional[FunctionCall] = None
    tool_calls: Optional[List[ToolCall]] = None
    tool_call_id: Optional[str] = None

    def get_text_content(self):
        if isinstance(self.content, str):
            return self.content
        elif isinstance(self.content, list):
            return '\n'.join(part.text for part in self.content if part.text is not None)
        else:
            raise ValueError('Unknown type of content')

    def openai_message(self):
        if isinstance(self.content, str):
            content = self.content
        elif isinstance(self.content, list):
            content = [part.dict(exclude_none=True) for part in self.content]
        elif self.content is None:
            content = None
        else:
            raise ValueError('Unknown type of content')

        data = {
            'role': self.role,
            'content': content
        }
        if self.name:
            data['name'] = self.name
        if self.function_call:
            data['function_call'] = {
                'name': self.function_call.name,
                'arguments': self.function_call.arguments,
            }
        if self.tool_calls:
            data['tool_calls'] = []
            for tool_call in self.tool_calls:
                data['tool_calls'].append({
                    'id': tool_call.id,
                    'type': tool_call.type,
                    'function': {
                        'name': tool_call.function.name,
                        'arguments': tool_call.function.arguments,
                    }
                })
        if self.tool_call_id:
            data['tool_call_id'] = self.tool_call_id
        return data


class ChatGPT:
    def __init__(self, llm_model, system_prompt: str, function_storage: FunctionStorage = None):
        self.function_storage = function_storage
        self.llm_model = llm_model
        self.system_prompt = system_prompt

    async def send_messages(self, messages_to_send: List[DialogMessage]) -> (DialogMessage, CompletionUsage):
        additional_fields = self.create_additional_fields()

        messages = self.create_context(messages_to_send, self.system_prompt)
        resp = await LLMClientFactory.get_client(self.llm_model.model_name).chat_completions_create(
            model=self.llm_model.model_name,
            messages=messages,
            temperature=settings.OPENAI_CHAT_COMPLETION_TEMPERATURE,
            **additional_fields,
        )
        completion_usage = CompletionUsage(model=self.llm_model.model_name, **dict(resp.usage))
        message = resp.choices[0].message
        response = DialogMessage(**dict(message))
        return response, completion_usage

    async def send_messages_streaming(self, messages_to_send: List[DialogMessage], is_cancelled: Callable[[], bool]) -> (DialogMessage, CompletionUsage):
        additional_fields = self.create_additional_fields()

        messages = self.create_context(messages_to_send, self.system_prompt)
        resp_generator = await LLMClientFactory.get_client(self.llm_model.model_name).chat_completions_create(
            model=self.llm_model.model_name,
            messages=messages,
            temperature=settings.OPENAI_CHAT_COMPLETION_TEMPERATURE,
            stream=True,
            **additional_fields,
        )

        # TODO: calculate function tokens
        prompt_tokens = count_messages_tokens(messages, self.llm_model.model_name)
        result_dict = {}
        async for resp_part in resp_generator:
            dialog_message = None
            completion_usage = None
            completion_tokens = None

            if resp_part.usage is not None:
                completion_usage = CompletionUsage(model=self.llm_model.model_name, **dict(resp_part.usage))

            delta = resp_part.choices[0].delta if resp_part.choices else None
            if delta and delta.content:
                result_dict = merge_dicts(result_dict, dict(delta))
                dialog_message = DialogMessage(**result_dict)
                completion_tokens = count_messages_tokens([result_dict], model=self.llm_model.model_name)
            if delta and delta.function_call is not None:
                if 'function_call' not in result_dict or result_dict['function_call'] is None:
                    result_dict['function_call'] = {}
                function_call_dict = merge_dicts(result_dict['function_call'], dict(delta.function_call))
                result_dict['function_call'] = function_call_dict
                dialog_message = DialogMessage(function_call=result_dict)
                # TODO: find more accurate way to calculate completion length for function calls
                completion_tokens = count_string_tokens(json.dumps(result_dict), model=self.llm_model.model_name)

            if not dialog_message and not completion_usage:
                # no updates at all, nothing to return
                continue
            elif not dialog_message:
                # no updates only in dialog message, create it from last result
                dialog_message = DialogMessage(**result_dict)
            elif not completion_usage:
                # no updates only in completion usage, calculate it from dialog message result
                completion_usage = CompletionUsage(
                    model=self.llm_model.model_name,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=prompt_tokens + completion_tokens,
                )

            # openai doesn't return this field in streaming mode somewhy
            dialog_message.role = 'assistant'
            yield dialog_message, completion_usage
            if is_cancelled():
                # some more tokens may be generated after cancellation
                completion_usage.completion_tokens += 20
                with suppress(BaseException):
                    # sometimes this call throws an exception since python 3.8
                    await resp_generator.response.aclose()
                break

    def create_additional_fields(self):
        additional_fields = {}
        if self.function_storage is not None:
            if self.llm_model.capabilities.tool_calling:
                additional_fields.update({
                    'tools': [
                        {
                            'type': 'function',
                            'function': function,
                        }
                        for function in self.function_storage.get_functions_info()
                    ]
                })
            elif self.llm_model.capabilities.function_calling:
                additional_fields.update({
                    'functions': self.function_storage.get_functions_info(),
                    'function_call': 'auto',
                })
        return additional_fields

    @staticmethod
    def create_context(messages: List[DialogMessage], system_prompt: str) -> List[Any]:
        result = [{"role": "system", "content": system_prompt}]
        result += [dialog_message.openai_message() for dialog_message in messages]
        return result


async def summarize_messages(messages: List[DialogMessage], model: str, summary_max_length: int) -> (str, CompletionUsage):
    prompt_messages = [m.openai_message() for m in messages]
    prompt_messages += [{
        "role": "user",
        "content": f"Summarize this conversation in {summary_max_length} characters or less. Divide different themes explicitly with new lines. Return only text of summary, nothing else.",
    }]
    resp = await LLMClientFactory.get_client(model).chat_completions_create(
        model=model,
        messages=prompt_messages,
        temperature=settings.OPENAI_CHAT_COMPLETION_TEMPERATURE,
        max_tokens=summary_max_length,
    )
    completion_usage = CompletionUsage(model=model, **dict(resp.usage))
    return resp.choices[0].message.content, completion_usage
