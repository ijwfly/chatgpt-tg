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


class DialogMessage(pydantic.BaseModel):
    role: str
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

    async def send_user_message(self, message_to_send: DialogMessage, previous_messages: List[DialogMessage] = None) -> DialogMessage:
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
                **additional_fields,
            )
            message = resp.choices[0].message
            response = DialogMessage(**message)
            return response
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
