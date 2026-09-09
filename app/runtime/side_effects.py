from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class SideEffectHandler(Protocol):
    async def send_message(self, text: str) -> int:
        ...

    async def send_photo(self, photo_bytes: bytes, caption: Optional[str] = None) -> int:
        ...

    async def send_document(self, document_bytes: bytes, filename: str, caption: Optional[str] = None) -> int:
        ...

    async def edit_message(self, message_id: int, text: str) -> None:
        ...

    async def download_file(self, file_id: str, max_bytes: Optional[int] = None) -> bytes:
        """Fetches a file the user sent (by transport file id), e.g. an image the model wants to process."""
        ...
