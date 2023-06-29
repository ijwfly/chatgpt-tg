from typing import List, Any

from app import settings

import pydantic
import openai


class GptModel:
    GPT_35_TURBO = 'gpt-3.5-turbo'
    GPT_35_TURBO_16K = 'gpt-3.5-turbo-16k'
    GPT_4 = 'gpt-4'


GPT_MODELS = {GptModel.GPT_35_TURBO, GptModel.GPT_35_TURBO_16K, GptModel.GPT_4}


class DialogueMessage(pydantic.BaseModel):
    role: str
    content: str

    def openai_message(self):
        return {
            'role': self.role,
            'content': self.content,
        }


class ChatGPT:
    def __init__(self, model="gpt-3.5-turbo"):
        self.set_model(model)

    def set_model(self, model="gpt-3.5-turbo"):
        if model not in GPT_MODELS:
            raise ValueError(f"Unknown model: {model}")
        self.model = model

    async def send_user_message(self, message_to_send: DialogueMessage, previous_messages: List[DialogueMessage] = None, gpt_mode="assistant") -> DialogueMessage:
        if previous_messages is None:
            previous_messages = []

        if gpt_mode not in settings.gpt_mode.keys():
            raise ValueError(f"GPT Mode {gpt_mode} not found in settings")

        if self.model not in GPT_MODELS:
            raise ValueError(f"Unknown model: {self.model}")

        messages = self.generate_prompt(message_to_send, previous_messages, gpt_mode)
        try:
            resp = await openai.ChatCompletion.acreate(
                model=self.model,
                messages=messages,
            )
            answer = resp.choices[0].message["content"].strip()
            response = DialogueMessage(role='assistant', content=answer)
            return response
        except openai.error.InvalidRequestError as e:
            # TODO: check for actual error
            raise ValueError("Too many tokens for current model") from e

    @staticmethod
    def generate_prompt(message: DialogueMessage, previous_messages: List[DialogueMessage], gpt_mode="assistant") -> List[Any]:
        system_prompt = settings.gpt_mode[gpt_mode]["system"]

        messages = [{"role": "system", "content": system_prompt}]
        for dialogue_message in previous_messages:
            messages.append(dialogue_message.openai_message())
        messages.append(message.openai_message())

        return messages
