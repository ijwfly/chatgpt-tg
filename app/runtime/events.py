from dataclasses import dataclass
from typing import Optional

from app.openai_helpers.chatgpt import DialogMessage


@dataclass
class RuntimeEvent:
    pass


@dataclass
class StreamingContentDelta(RuntimeEvent):
    visible_text: str
    thinking_text: str
    is_thinking: bool


@dataclass
class FinalResponse(RuntimeEvent):
    dialog_message: DialogMessage
    needs_context_save: bool = True  # True if content needs to be saved to context by the adapter


@dataclass
class FunctionCallStarted(RuntimeEvent):
    function_name: str
    function_args: str
    tool_call_id: Optional[str] = None


@dataclass
class FunctionCallCompleted(RuntimeEvent):
    function_name: str
    function_args: str
    result: Optional[str] = None
    tool_call_id: Optional[str] = None


@dataclass
class ErrorEvent(RuntimeEvent):
    error: Exception
    message: str
