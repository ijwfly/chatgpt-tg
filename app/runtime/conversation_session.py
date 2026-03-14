from dataclasses import dataclass
from typing import Optional


@dataclass
class ConversationSession:
    """Transport-agnostic conversation identification."""
    chat_id: int
    reply_to_message_id: Optional[int] = None
    is_forwarded: bool = False
