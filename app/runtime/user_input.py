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
class VoiceTranscription:
    text: str
    tg_message_id: int = -1
    # other transport message ids that should lead to this context message on reply
    # (e.g. the user's own voice message, which the bot echoed the transcription for)
    alias_tg_message_ids: List[int] = field(default_factory=list)


@dataclass
class SandboxFileInput:
    """A file saved to the user's bash sandbox workspace."""
    filename: str
    size: int
    tg_message_id: int = -1
    # user's caption on the document message — part of the same context message
    caption: Optional[str] = None
    # other transport message ids that should lead to this context message on reply
    # (e.g. bot's "Saved to agent workspace" confirmation)
    alias_tg_message_ids: List[int] = field(default_factory=list)


@dataclass
class UserInput:
    """Transport-agnostic user input batch."""
    text_inputs: List[TextInput] = field(default_factory=list)
    voice_transcriptions: List[VoiceTranscription] = field(default_factory=list)
    sandbox_files: List[SandboxFileInput] = field(default_factory=list)
    # transport hint: user-authored content was captured that needs an answer even though the batch
    # does not look like a prompt (e.g. a document with a caption). Runtimes ignore this flag.
    force_prompt: bool = False

    @property
    def has_content(self) -> bool:
        return bool(self.text_inputs or self.voice_transcriptions or self.sandbox_files)
