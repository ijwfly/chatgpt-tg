from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class ImageInput:
    file_id: str
    width: int
    height: int


@dataclass
class TextInput:
    """A single text (and optionally image) message from the user."""
    text: Optional[str] = None
    tg_message_id: int = -1
    images: Optional[List[ImageInput]] = None


@dataclass
class DocumentInput:
    document_id: str
    document_name: str
    tg_message_id: int = -1


@dataclass
class VoiceTranscription:
    text: str
    tg_message_id: int = -1


@dataclass
class UserInput:
    """Transport-agnostic user input batch."""
    text_inputs: List[TextInput] = field(default_factory=list)
    documents: List[DocumentInput] = field(default_factory=list)
    voice_transcriptions: List[VoiceTranscription] = field(default_factory=list)

    @property
    def has_content(self) -> bool:
        return bool(self.text_inputs or self.documents or self.voice_transcriptions)
