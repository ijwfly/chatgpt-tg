from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ConversationSession:
    """Transport-agnostic conversation identification."""
    chat_id: int
    reply_to_message_id: Optional[int] = None
    is_forwarded: bool = False
    # Explicit dialog branch to load as context (db message ids), bypassing
    # last-message lookup and expiration. Used by scheduled task execution.
    context_message_ids: Optional[List[int]] = None
