from typing import List, Any, Optional

import settings

import pydantic
import openai

from app.openai_helpers.function_storage import FunctionStorage


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

    async def send_user_message(self, message_to_send: DialogMessage, previous_messages: List[DialogMessage] = None) -> (DialogMessage, CompletionUsage):
        additional_fields = {}
        if self.function_storage is not None:
            additional_fields.update({
                'functions': self.function_storage.get_openai_prompt(),
                'function_call': 'auto',
            })

        if previous_messages is None:
            previous_messages = []

        messages = self.create_context(message_to_send, previous_messages, self.gpt_mode)
        try:
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
        except openai.error.InvalidRequestError:
            # TODO: check for error
            raise

    @staticmethod
    def create_context(message: DialogMessage, previous_messages: List[DialogMessage], gpt_mode) -> List[Any]:
        system_prompt = settings.gpt_mode[gpt_mode]["system"]

        messages = [{"role": "system", "content": system_prompt}]
        for dialog_message in previous_messages:
            messages.append(dialog_message.openai_message())
        messages.append(message.openai_message())

        return messages


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
