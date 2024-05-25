import json
from contextlib import suppress
from typing import List, Any, Optional, Callable, Union

import settings
from app.bot.utils import merge_dicts
from app.llm_models import LLModel, get_model_by_name
from app.openai_helpers.count_tokens import count_messages_tokens, count_tokens_from_functions, count_string_tokens
from app.openai_helpers.function_storage import FunctionStorage

import pydantic

from app.openai_helpers.llm_client import OpenAILLMClient


class FunctionCall(pydantic.BaseModel):
    name: str
    arguments: str


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
        return data


class ChatGPT:
    def __init__(self, model, system_prompt: str, function_storage: FunctionStorage = None):
        self.function_storage = function_storage
        self.llm_model = get_model_by_name(model)
        self.system_prompt = system_prompt

    async def send_messages(self, messages_to_send: List[DialogMessage]) -> (DialogMessage, CompletionUsage):
        additional_fields = {}
        if self.function_storage is not None:
            if self.llm_model.capabilities.function_calling:
                additional_fields.update({
                    'functions': self.function_storage.get_functions_info(),
                    'function_call': 'auto',
                })
            elif self.llm_model.capabilities.tool_calling:
                NotImplementedError('Tool calling support is not implemented yet')

        messages = self.create_context(messages_to_send, self.system_prompt)
        resp = await OpenAILLMClient.get_client(self.llm_model.model_name).chat.completions.create(
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
        prompt_tokens = 0

        additional_fields = {}
        if self.function_storage is not None:
            if self.llm_model.capabilities.function_calling:
                functions = self.function_storage.get_functions_info()
                prompt_tokens += count_tokens_from_functions(functions, self.llm_model.model_name)
                additional_fields.update({
                    'functions': functions,
                    'function_call': 'auto',
                })
            elif self.llm_model.capabilities.tool_calling:
                NotImplementedError('Tool calling support is not implemented yet')

        messages = self.create_context(messages_to_send, self.system_prompt)
        resp_generator = await OpenAILLMClient.get_client(self.llm_model.model_name).chat.completions.create(
            model=self.llm_model.model_name,
            messages=messages,
            temperature=settings.OPENAI_CHAT_COMPLETION_TEMPERATURE,
            stream=True,
            **additional_fields,
        )

        prompt_tokens += count_messages_tokens(messages, self.llm_model.model_name)
        result_dict = {}
        async for resp_part in resp_generator:
            delta = resp_part.choices[0].delta
            if not delta:
                continue

            if delta.content is not None:
                if not delta.content:
                    continue
                result_dict = merge_dicts(result_dict, dict(delta))
                dialog_message = DialogMessage(**result_dict)
                completion_tokens = count_messages_tokens([result_dict], model=self.llm_model.model_name)
            elif delta.function_call is not None:
                result_dict = merge_dicts(result_dict, dict(delta.function_call))
                dialog_message = DialogMessage(function_call=result_dict)
                # TODO: find mode accurate way to calculate completion length for function calls
                completion_tokens = count_string_tokens(json.dumps(result_dict), model=self.llm_model.model_name)
            else:
                continue

            # openai doesn't return this field in streaming mode somewhy
            dialog_message.role = 'assistant'
            completion_usage = CompletionUsage(
                model=self.llm_model.model_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            )
            yield dialog_message, completion_usage
            if is_cancelled():
                # some more tokens may be generated after cancellation
                completion_usage.completion_tokens += 20
                with suppress(BaseException):
                    # sometimes this call throws an exception since python 3.8
                    await resp_generator.response.aclose()
                break

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
    resp = await OpenAILLMClient.get_client(model).chat.completions.create(
        model=model,
        messages=prompt_messages,
        temperature=settings.OPENAI_CHAT_COMPLETION_TEMPERATURE,
        max_tokens=summary_max_length,
    )
    completion_usage = CompletionUsage(model=model, **dict(resp.usage))
    return resp.choices[0].message.content, completion_usage
