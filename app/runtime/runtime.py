from typing import Protocol, AsyncGenerator, Callable, Optional

from app.runtime.conversation_session import ConversationSession
from app.runtime.events import RuntimeEvent
from app.runtime.user_input import UserInput


class LLMRuntime(Protocol):
    """
    Black box: receives user input, loads context,
    calls LLM, executes tools, saves results, yields events.
    """
    async def process_turn(
        self,
        user_input: UserInput,
        session: ConversationSession,
        is_cancelled: Callable[[], bool],
    ) -> AsyncGenerator[RuntimeEvent, None]:
        ...
