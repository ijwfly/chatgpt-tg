"""Naming helpers for files placed into a user's sandbox workspace.

Shared by the incoming-document path (app/bot/batched_input_handler.py) and the agent tools that
write files there, so both sanitize names and resolve collisions the same way.
"""
import os
import re


def sanitize_workspace_path(path: str) -> str:
    """Makes a name supplied by the user or the model safe to use as a workspace-relative path.

    Absolute paths and `..` segments are dropped; the sandbox confines paths anyway, this only
    keeps the result predictable.
    """
    parts = []
    for part in (path or '').replace('\\', '/').split('/'):
        if part in ('', '.', '..'):
            continue
        # \w is unicode-aware: keeps letters in any alphabet (incl. cyrillic) and digits
        part = re.sub(r'[^\w.-]', '_', part)
        if not part.strip('._'):
            part = 'file'
        parts.append(part)
    return '/'.join(parts) or 'file'


def sanitize_workspace_filename(filename: str) -> str:
    """Same, but for names that must stay a single file in the workspace root."""
    return sanitize_workspace_path(os.path.basename(filename or ''))


async def unique_workspace_name(sandbox_client, telegram_user_id: int, path: str) -> str:
    """Appends _1, _2, ... until the path is free, so uploads never overwrite existing files."""
    base, ext = os.path.splitext(path)
    candidate = path
    counter = 1
    while (await sandbox_client.stat(telegram_user_id, candidate)).get('type') != 'missing':
        candidate = f'{base}_{counter}{ext}'
        counter += 1
    return candidate
