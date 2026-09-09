"""
Keeps the dialog history valid for the LLM APIs when a tool call was left without a result.

An assistant message with `tool_calls` is saved before the tools run, and every tool result is
saved after its tool finishes. If the turn is interrupted in between (bot restart, task cancellation,
a transport error while the tool hint is being shown) the assistant message stays in the history with
no matching `tool` messages — and both OpenAI and Anthropic reject such a request on every later turn
of that dialog branch. The runtimes try to close such calls themselves (see `close_unanswered_tool_calls`
in `app/runtime/context_utils.py`); this module is the safety net for histories that are already broken.
"""
from typing import List, Optional, Set

from app.openai_helpers.chatgpt import DialogMessage

INTERRUPTED_TOOL_CALL_RESULT = 'Error: the tool call was interrupted before it produced a result.'


def interrupted_tool_response(tool_call_id: str) -> DialogMessage:
    return DialogMessage(role='tool', tool_call_id=tool_call_id, content=INTERRUPTED_TOOL_CALL_RESULT)


def repair_dangling_tool_calls(messages: List[DialogMessage]) -> List[DialogMessage]:
    """
    Returns the messages with a synthetic error `tool` message inserted for every tool call that has no
    result. The inserted messages go right after the tool results of the same assistant message (or right
    after the assistant message when none of its calls were answered), so the group stays contiguous — the
    Anthropic converter needs all tool results in the next message. The input list is not modified.
    """
    result: List[DialogMessage] = []
    i = 0
    while i < len(messages):
        message = messages[i]
        result.append(message)
        i += 1
        if message.role != 'assistant' or not message.tool_calls:
            continue

        expected = [tool_call.id for tool_call in message.tool_calls if tool_call.id]
        answered: Set[Optional[str]] = set()
        while i < len(messages) and messages[i].role == 'tool':
            answered.add(messages[i].tool_call_id)
            result.append(messages[i])
            i += 1

        for tool_call_id in expected:
            if tool_call_id not in answered:
                result.append(interrupted_tool_response(tool_call_id))
    return result
