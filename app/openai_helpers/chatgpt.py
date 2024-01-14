import json
from contextlib import suppress
from typing import List, Any, Optional, Callable, Union

import settings
from app.bot.utils import merge_dicts
from app.openai_helpers.count_tokens import count_messages_tokens, count_tokens_from_functions, count_string_tokens
from app.openai_helpers.function_storage import FunctionStorage

import pydantic

from app.openai_helpers.utils import OpenAIAsync


class GptModel:
    GPT_35_TURBO = 'gpt-3.5-turbo'
    GPT_35_TURBO_16K = 'gpt-3.5-turbo-16k'
    GPT_4 = 'gpt-4'
    GPT_4_TURBO_PREVIEW = 'gpt-4-1106-preview'
    GPT_4_VISION_PREVIEW = 'gpt-4-vision-preview'


GPT_MODELS = {GptModel.GPT_35_TURBO, GptModel.GPT_35_TURBO_16K, GptModel.GPT_4,
              GptModel.GPT_4_TURBO_PREVIEW, GptModel.GPT_4_VISION_PREVIEW}


class FunctionCall(pydantic.BaseModel):
    name: str
    arguments: str


class CompletionUsage(pydantic.BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    model: str


class DialogMessageContentPart(pydantic.BaseModel):
    type: str
    text: Optional[str] = None
    image_url: Optional[str] = None


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
        if model not in GPT_MODELS:
            raise ValueError(f"Unknown model: {model}")
        self.model = model
        self.system_prompt = system_prompt

    async def send_messages(self, messages_to_send: List[DialogMessage]) -> (DialogMessage, CompletionUsage):
        additional_fields = {}
        if self.function_storage is not None:
            additional_fields.update({
                'functions': self.function_storage.get_openai_prompt(),
                'function_call': 'auto',
            })

        if self.model == GptModel.GPT_4_VISION_PREVIEW:
            # TODO: somewhy by default it's 16 tokens for this model
            additional_fields['max_tokens'] = 4096

            # TODO: vision preview doesn't support function calls
            if 'function_call' in additional_fields:
                del additional_fields['function_call']
            if 'functions' in additional_fields:
                del additional_fields['functions']

        messages = self.create_context(messages_to_send, self.system_prompt)
        resp = await OpenAIAsync.instance().chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=settings.OPENAI_CHAT_COMPLETION_TEMPERATURE,
            **additional_fields,
        )
        completion_usage = CompletionUsage(model=self.model, **dict(resp.usage))
        message = resp.choices[0].message
        response = DialogMessage(**dict(message))
        return response, completion_usage

    async def send_messages_streaming(self, messages_to_send: List[DialogMessage], is_cancelled: Callable[[], bool]) -> (DialogMessage, CompletionUsage):
        prompt_tokens = 0

        additional_fields = {}
        system_prompt_addition = None
        if self.function_storage is not None:
            system_prompt_addition = self.function_storage.get_system_prompt_addition()
            functions = self.function_storage.get_openai_prompt()
            prompt_tokens += count_tokens_from_functions(functions, self.model)
            additional_fields.update({
                'functions': self.function_storage.get_openai_prompt(),
                'function_call': 'auto',
            })

        if self.model == GptModel.GPT_4_VISION_PREVIEW:
            # TODO: somewhy by default it's 16 tokens for this model
            additional_fields['max_tokens'] = 4096

            # TODO: vision preview doesn't support function calls
            if 'function_call' in additional_fields:
                del additional_fields['function_call']
            if 'functions' in additional_fields:
                del additional_fields['functions']

        messages = self.create_context(messages_to_send, self.system_prompt)
        resp_generator = await OpenAIAsync.instance().chat.completions.create(
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

            if delta.content is not None:
                if not delta.content:
                    continue
                result_dict = merge_dicts(result_dict, dict(delta))
                dialog_message = DialogMessage(**result_dict)
                completion_tokens = count_messages_tokens([result_dict], model=self.model)
            elif delta.function_call is not None:
                result_dict = merge_dicts(result_dict, dict(delta.function_call))
                dialog_message = DialogMessage(function_call=result_dict)
                # TODO: find mode accurate way to calculate completion length for function calls
                completion_tokens = count_string_tokens(json.dumps(result_dict), model=self.model)
            else:
                continue

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
    resp = await OpenAIAsync.instance().chat.completions.create(
        model=model,
        messages=prompt_messages,
        temperature=settings.OPENAI_CHAT_COMPLETION_TEMPERATURE,
        max_tokens=summary_max_length,
    )
    completion_usage = CompletionUsage(model=model, **dict(resp.usage))
    return resp.choices[0].message.content, completion_usage
