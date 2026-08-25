from typing import Optional

import pydantic
from abc import ABC, abstractmethod

from app.runtime.side_effects import SideEffectHandler


class OpenAIFunctionParams(pydantic.BaseModel):
    pass


class OpenAIFunction(ABC):
    PARAMS_SCHEMA = OpenAIFunctionParams

    def __init__(self, user, db, context_manager, side_effects: SideEffectHandler, tool_call_id: str = None):
        self.user = user
        self.db = db
        self.context_manager = context_manager
        self.side_effects = side_effects
        self.tool_call_id = tool_call_id
        # A function may set this in run() to the transport message id that represents its result
        # (e.g. a document sent to chat). The runtime saves the function/tool response with this id
        # instead of -1, so the user can reply to that message and continue the dialog branch.
        self.result_tg_message_id: Optional[int] = None

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
            params = self.PARAMS_SCHEMA.model_validate_json(params)
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
        params_schema = cls.PARAMS_SCHEMA.model_json_schema()
        return params_schema

    @classmethod
    def get_system_prompt_addition(cls) -> Optional[str]:
        """
        Returns text to add to system prompt when this function is added to context. You can use this to add
        additional instructions about how to use this function.
        """
        return None

    @classmethod
    def get_status_message(cls) -> str:
        """
        Short, user-facing hint shown in chat while the function is running
        (e.g. "Searching the web..."). Override per function for clarity.
        """
        name = cls.get_name().replace('_', ' ').strip()
        return f'Running {name}...'
