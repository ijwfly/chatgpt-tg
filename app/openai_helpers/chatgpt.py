import json
from typing import List, Any, Optional, Callable

import settings
from app.bot.utils import merge_dicts
from app.openai_helpers.count_tokens import count_messages_tokens, count_tokens_from_functions, count_string_tokens
from app.openai_helpers.function_storage import FunctionStorage

import pydantic
import openai


class GptModel:
    GPT_35_TURBO = 'gpt-3.5-turbo'
    GPT_35_TURBO_16K = 'gpt-3.5-turbo-16k'
    GPT_4 = 'gpt-4'


GPT_MODELS = {GptModel.GPT_35_TURBO, GptModel.GPT_35_TURBO_16K, GptModel.GPT_4}


class FunctionCall(pydantic.BaseModel):
    name: str
    arguments: str


class CompletionUsage(pydantic.BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    model: str


class DialogMessage(pydantic.BaseModel):
    role: Optional[str] = None
    name: Optional[str] = None
    content: Optional[str] = None
    function_call: Optional[FunctionCall] = None

    def openai_message(self):
        data = {
            'role': self.role,
            'content': self.content,
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
    def __init__(self, model="gpt-3.5-turbo", gpt_mode="assistant", function_storage: FunctionStorage = None):
        self.function_storage = function_storage
        if model not in GPT_MODELS:
            raise ValueError(f"Unknown model: {model}")
        self.model = model
        if gpt_mode not in settings.gpt_mode:
            raise ValueError(f"Unknown GPT mode: {gpt_mode}")
        self.gpt_mode = gpt_mode

    async def send_messages(self, messages_to_send: List[DialogMessage]) -> (DialogMessage, CompletionUsage):
        additional_fields = {}
        if self.function_storage is not None:
            additional_fields.update({
                'functions': self.function_storage.get_openai_prompt(),
                'function_call': 'auto',
            })

        messages = self.create_context(messages_to_send, self.gpt_mode)
        resp = await openai.ChatCompletion.acreate(
            model=self.model,
            messages=messages,
            temperature=settings.OPENAI_CHAT_COMPLETION_TEMPERATURE,
            **additional_fields,
        )
        completion_usage = CompletionUsage(model=self.model, **resp.usage)
        message = resp.choices[0].message
        response = DialogMessage(**message)
        return response, completion_usage

    async def send_messages_streaming(self, messages_to_send: List[DialogMessage], is_cancelled: Callable[[], bool]) -> (DialogMessage, CompletionUsage):
        prompt_tokens = 0

        additional_fields = {}
        if self.function_storage is not None:
            functions = self.function_storage.get_openai_prompt()
            prompt_tokens += count_tokens_from_functions(functions, self.model)
            additional_fields.update({
                'functions': self.function_storage.get_openai_prompt(),
                'function_call': 'auto',
            })

        messages = self.create_context(messages_to_send, self.gpt_mode)
        resp_generator = await openai.ChatCompletion.acreate(
            model=self.model,
            messages=messages,
            temperature=settings.OPENAI_CHAT_COMPLETION_TEMPERATURE,
            stream=True,
            **additional_fields,
        )

        prompt_tokens += count_messages_tokens(messages, self.model)
        result_dict = {}
        async for resp_part in resp_generator:
            delta = resp_part.choices[0].delta
            if not delta:
                continue

            if 'content' in delta and delta.content is not None:
                if not delta.content:
                    continue
                result_dict = merge_dicts(result_dict, dict(delta))
                dialog_message = DialogMessage(**result_dict)
                completion_tokens = count_messages_tokens([result_dict], model=self.model)
            elif 'function_call' in delta and delta.function_call is not None:
                result_dict = merge_dicts(result_dict, dict(delta.function_call))
                dialog_message = DialogMessage(function_call=result_dict)
                # TODO: find mode accurate way to calculate completion length for function calls
                completion_tokens = count_string_tokens(json.dumps(result_dict), model=self.model)
            else:
                raise ValueError('Unknown type of gpt response')

            # openai doesn't return this field in streaming mode somewhy
            dialog_message.role = 'assistant'
            completion_usage = CompletionUsage(
                model=self.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            )
            yield dialog_message, completion_usage
            if is_cancelled():
                # some more tokens will be generated after cancellation
                completion_usage.completion_tokens += 20
                break

    @staticmethod
    def create_context(messages: List[DialogMessage], gpt_mode) -> List[Any]:
        system_prompt = settings.gpt_mode[gpt_mode]["system"]

        result = [{"role": "system", "content": system_prompt}]
        for dialog_message in messages:
            result.append(dialog_message.openai_message())

        return result


async def summarize_messages(messages: List[DialogMessage], model: str, summary_max_length: int) -> (str, CompletionUsage):
    prompt_messages = [m.openai_message() for m in messages]
    prompt_messages += [{
        "role": "user",
        "content": f"Summarize this conversation in {summary_max_length} characters or less. Divide different themes explicitly with new lines. Return only text of summary, nothing else.",
    }]
    resp = await openai.ChatCompletion.acreate(
        model=model,
        messages=prompt_messages,
        temperature=settings.OPENAI_CHAT_COMPLETION_TEMPERATURE,
    )
    completion_usage = CompletionUsage(model=model, **resp.usage)
    return resp.choices[0].message.content, completion_usage
