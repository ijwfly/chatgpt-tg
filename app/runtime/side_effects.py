from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class SideEffectHandler(Protocol):
    async def send_message(self, text: str) -> int:
        ...

    async def send_photo(self, photo_bytes: bytes, caption: Optional[str] = None) -> int:
        ...
