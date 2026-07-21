"""HTTP client for the sandbox service (see sandbox/).

Each user is identified by their telegram id passed in the X-User-Id header;
the sandbox maps it to an isolated linux user with a private workspace.
"""

import os
import re
from typing import Optional, Tuple
from urllib.parse import quote

import httpx

import settings


class SandboxError(Exception):
    pass


def _extract_error(response: httpx.Response) -> str:
    try:
        return response.json().get('error', response.text)
    except Exception:
        return response.text or f'HTTP {response.status_code}'


class SandboxClient:
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or settings.SANDBOX_URL).rstrip('/')

    @staticmethod
    def _headers(telegram_user_id) -> dict:
        return {'X-User-Id': str(telegram_user_id)}

    async def exec(self, telegram_user_id, command: str, timeout: int) -> dict:
        try:
            async with httpx.AsyncClient(timeout=timeout + 30) as client:
                response = await client.post(
                    f'{self.base_url}/exec',
                    json={'command': command, 'timeout': timeout},
                    headers=self._headers(telegram_user_id),
                )
        except httpx.HTTPError as e:
            raise SandboxError(f'Sandbox unavailable: {e}')
        if response.status_code != 200:
            raise SandboxError(_extract_error(response))
        return response.json()

    async def _fileop(self, telegram_user_id, payload: dict) -> dict:
        try:
            async with httpx.AsyncClient(timeout=settings.SANDBOX_REQUEST_TIMEOUT) as client:
                response = await client.post(
                    f'{self.base_url}/fileop',
                    json=payload,
                    headers=self._headers(telegram_user_id),
                )
        except httpx.HTTPError as e:
            raise SandboxError(f'Sandbox unavailable: {e}')
        if response.status_code != 200:
            raise SandboxError(_extract_error(response))
        result = response.json()
        if 'error' in result:
            raise SandboxError(result['error'])
        return result

    async def read_file(self, telegram_user_id, path: str, limit: int = 0) -> dict:
        return await self._fileop(telegram_user_id, {'op': 'read', 'path': path, 'limit': limit})

    async def write_file(self, telegram_user_id, path: str, content: str) -> dict:
        return await self._fileop(telegram_user_id, {'op': 'write', 'path': path, 'content': content})

    async def edit_file(self, telegram_user_id, path: str, old_text: str, new_text: str) -> dict:
        return await self._fileop(
            telegram_user_id, {'op': 'edit', 'path': path, 'old_text': old_text, 'new_text': new_text}
        )

    async def stat(self, telegram_user_id, path: str) -> dict:
        return await self._fileop(telegram_user_id, {'op': 'stat', 'path': path})

    async def upload_file(self, telegram_user_id, rel_path: str, data: bytes) -> dict:
        try:
            async with httpx.AsyncClient(timeout=settings.SANDBOX_REQUEST_TIMEOUT) as client:
                response = await client.put(
                    f'{self.base_url}/files/{quote(rel_path)}',
                    content=data,
                    headers=self._headers(telegram_user_id),
                )
        except httpx.HTTPError as e:
            raise SandboxError(f'Sandbox unavailable: {e}')
        if response.status_code != 200:
            raise SandboxError(_extract_error(response))
        return response.json()

    async def download_file(self, telegram_user_id, rel_path: str, max_bytes: int) -> Tuple[bytes, str]:
        """Download a workspace file. Returns (data, filename)."""
        try:
            async with httpx.AsyncClient(timeout=settings.SANDBOX_REQUEST_TIMEOUT) as client:
                async with client.stream(
                    'GET',
                    f'{self.base_url}/files/{quote(rel_path)}',
                    headers=self._headers(telegram_user_id),
                ) as response:
                    if response.status_code != 200:
                        await response.aread()
                        raise SandboxError(_extract_error(response))
                    if response.headers.get('content-type', '').startswith('application/json'):
                        # directory listing came back instead of file contents
                        raise SandboxError(f'Is a directory: {rel_path}')

                    chunks = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise SandboxError(
                                f'File is too big to send: {rel_path} exceeds {max_bytes} bytes'
                            )
                        chunks.append(chunk)

                    filename = os.path.basename(rel_path)
                    disposition = response.headers.get('content-disposition', '')
                    match = re.search(r'filename="([^"]+)"', disposition)
                    if match:
                        filename = match.group(1)
                    return b''.join(chunks), filename
        except httpx.HTTPError as e:
            raise SandboxError(f'Sandbox unavailable: {e}')
