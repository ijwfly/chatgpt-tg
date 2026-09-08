import json
from typing import Optional

import pydantic
from abc import ABC, abstractmethod

import settings
from app.runtime.side_effects import SideEffectHandler


class OpenAIFunctionParams(pydantic.BaseModel):
    pass


class OpenAIFunction(ABC):
    PARAMS_SCHEMA = OpenAIFunctionParams
    # Parameter whose value best answers "what is happening right now" for this call. It is
    # appended to the status message shown in chat while the function runs.
    STATUS_DETAIL_PARAM: Optional[str] = None

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

    @classmethod
    def get_status_detail(cls, params: dict) -> Optional[str]:
        """
        Per-call addition to the status message: what exactly is being searched, run or read.
        Override when the detail has to be built from several parameters.
        """
        if not cls.STATUS_DETAIL_PARAM:
            return None
        return params.get(cls.STATUS_DETAIL_PARAM)


def _clean_status_detail(detail) -> str:
    """One-line, length-capped version of a tool argument."""
    if detail is None or isinstance(detail, bool):
        return ''
    text = ' '.join(str(detail).split())
    limit = settings.FUNCTION_HINT_DETAIL_MAX_CHARS
    if len(text) > limit:
        text = text[:limit].rstrip() + '…'
    return text


def build_status_message(function_class, function_args: str) -> str:
    """Status hint for one tool call: the function's title plus a short detail of this call.

    Never raises: a hint is cosmetic and must not break the tool call it describes.
    """
    title = function_class.get_status_message()
    detail = None
    try:
        params = json.loads(function_args) if function_args else {}
        if isinstance(params, dict):
            detail = function_class.get_status_detail(params)
    except Exception:
        detail = None

    detail = _clean_status_detail(detail)
    if not detail:
        return title
    return f'{title.rstrip(". ")}: {detail}'
