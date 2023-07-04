from typing import List, Any

from app import settings

import pydantic
import openai


class GptModel:
    GPT_35_TURBO = 'gpt-3.5-turbo'
    GPT_35_TURBO_16K = 'gpt-3.5-turbo-16k'
    GPT_4 = 'gpt-4'


GPT_MODELS = {GptModel.GPT_35_TURBO, GptModel.GPT_35_TURBO_16K, GptModel.GPT_4}


class DialogMessage(pydantic.BaseModel):
    role: str
    content: str

    def openai_message(self):
        return {
            'role': self.role,
            'content': self.content,
        }


class ChatGPT:
    def __init__(self, model="gpt-3.5-turbo", gpt_mode="assistant"):
        if model not in GPT_MODELS:
            raise ValueError(f"Unknown model: {model}")
        self.model = model
        if gpt_mode not in settings.gpt_mode:
            raise ValueError(f"Unknown GPT mode: {gpt_mode}")
        self.gpt_mode = gpt_mode

    async def send_user_message(self, message_to_send: DialogMessage, previous_messages: List[DialogMessage] = None) -> DialogMessage:
        if previous_messages is None:
            previous_messages = []

        if self.model not in GPT_MODELS:
            raise ValueError(f"Unknown model: {self.model}")

        messages = self.generate_prompt(message_to_send, previous_messages, self.gpt_mode)
        try:
            resp = await openai.ChatCompletion.acreate(
                model=self.model,
                messages=messages,
            )
            answer = resp.choices[0].message["content"].strip()
            response = DialogMessage(role="assistant", content=answer)
            return response
        except openai.error.InvalidRequestError:
            # TODO: check for error
            raise

    @staticmethod
    def generate_prompt(message: DialogMessage, previous_messages: List[DialogMessage], gpt_mode) -> List[Any]:
        system_prompt = settings.gpt_mode[gpt_mode]["system"]

        messages = [{"role": "system", "content": system_prompt}]
        for dialog_message in previous_messages:
            messages.append(dialog_message.openai_message())
        messages.append(message.openai_message())

        return messages
