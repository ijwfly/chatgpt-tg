from typing import Optional

import pydantic
from aiogram.types import Message
from abc import ABC, abstractmethod


class OpenAIFunctionParams(pydantic.BaseModel):
    pass


class OpenAIFunction(ABC):
    PARAMS_SCHEMA = OpenAIFunctionParams

    def __init__(self, user, db, context_manager, message: Message, tool_call_id: str = None):
        self.user = user
        self.db = db
        self.context_manager = context_manager
        self.message = message
        self.tool_call_id = tool_call_id

    @abstractmethod
    async def run(self, params: OpenAIFunctionParams) -> Optional[str]:
        pass

    async def run_dict_args(self, params: dict):
        try:
            params = self.PARAMS_SCHEMA(**params)
        except Exception as e:
            return f"Parsing error: {e}"
        return await self.run(params)

    async def run_str_args(self, params: str):
        try:
            params = self.PARAMS_SCHEMA.parse_raw(params)
        except Exception as e:
            return f"Parsing error: {e}"
        return await self.run(params)

    @classmethod
    @abstractmethod
    def get_description(cls) -> str:
        pass

    @classmethod
    def get_name(cls) -> str:
        return cls.__name__

    @classmethod
    def get_params_schema(cls) -> dict:
        params_schema = cls.PARAMS_SCHEMA.schema()
        return params_schema

    @classmethod
    def get_system_prompt_addition(cls) -> Optional[str]:
        """
        Returns text to add to system prompt when this function is added to context. You can use this to add
        additional instructions about how to use this function.
        """
        return None
