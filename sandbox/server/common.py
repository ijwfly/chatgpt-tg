"""Shared config, logging, user provisioning and path resolution.

Adapted from the bash-mcp reference project. Auth layers (JWT, registry,
admin API) are dropped: this service lives on the internal docker network
and trusts the X-User-Id header set by the bot.
"""

import asyncio
import json
import logging
import os
import pwd
import re
import signal
import subprocess
from contextvars import ContextVar
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sandbox")

PORT = int(os.environ.get("SANDBOX_PORT", "8080"))
BASH_TIMEOUT_MAX = int(os.environ.get("BASH_TIMEOUT_MAX", "300"))

WORKSPACE_ROOT = "/workspace"
# Shared read-only skills, synced from the image by entrypoint.sh. Lives next to the
# per-user workspaces on the same volume; readable by everyone, writable by root only.
PUBLIC_SKILLS_DIR = f"{WORKSPACE_ROOT}/public_skills"
PERSONAL_SKILLS_DIRNAME = "skills"
HELPER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "file_helper.py")

current_user: ContextVar[str] = ContextVar("current_user")

# Linux usernames are capped at 32 chars; "user_" prefix leaves 27.
LINUX_USER_RE = re.compile(r"^user_[a-z0-9_]{1,27}$")


class PathOutsideWorkspace(Exception):
    pass


def sanitize_username(user_id: str) -> Optional[str]:
    cleaned = re.sub(r"[^a-z0-9]", "_", user_id.lower())[:27]
    if not cleaned:
        return None
    linux_user = f"user_{cleaned}"
    if not LINUX_USER_RE.match(linux_user):
        return None
    return linux_user


def get_current_user() -> Optional[str]:
    try:
        return current_user.get()
    except LookupError:
        return None


def workspace_for(linux_user: str) -> str:
    return f"{WORKSPACE_ROOT}/{linux_user}"


def ensure_user(linux_user: str) -> None:
    """Create OS user and workspace directory if they don't exist yet.

    Checks the OS user via `id`, not the directory — the workspace volume
    persists across container recreations while OS users do not.
    """
    r = subprocess.run(["id", linux_user], capture_output=True)
    if r.returncode != 0:
        subprocess.run(
            ["useradd", "-m", "-s", "/bin/bash", "-G", "mcpusers", linux_user],
            check=False,
            capture_output=True,
        )
        logger.info("provisioned linux user=%s", linux_user)
    workspace = workspace_for(linux_user)
    os.makedirs(workspace, exist_ok=True)
    try:
        os.chmod(workspace, 0o700)
    except OSError:
        pass  # bind mounts on some hosts (macOS) may not support chmod
    try:
        uid = pwd.getpwnam(linux_user).pw_uid
        if os.stat(workspace).st_uid != uid:
            subprocess.run(["chown", "-R", f"{linux_user}:{linux_user}", workspace], check=False)
    except (KeyError, OSError):
        pass


def _is_inside(resolved: str, base: str) -> bool:
    return resolved == base or resolved.startswith(base + os.sep)


def resolve_path(path: str, allow_public: bool = False) -> tuple:
    """Resolve an API path against the caller's workspace.

    Returns (absolute_path, linux_user). Raises PathOutsideWorkspace if the
    resolved path (symlinks included) escapes the workspace. With allow_public
    the shared skills directory is accepted too — read-only operations only,
    the caller decides.
    """
    linux_user = get_current_user()
    base = workspace_for(linux_user)

    if not os.path.isabs(path):
        path = os.path.join(base, path)
    resolved = os.path.realpath(path)

    if _is_inside(resolved, base):
        return resolved, linux_user
    if allow_public and _is_inside(resolved, PUBLIC_SKILLS_DIR):
        return resolved, linux_user
    raise PathOutsideWorkspace(
        f"Path outside workspace: {path!r} (workspace is {base})"
    )


def personal_skills_dir(linux_user: str) -> str:
    return os.path.join(workspace_for(linux_user), PERSONAL_SKILLS_DIRNAME)


def helper_cmd(linux_user: str, *args: str) -> list:
    """Build the file_helper.py invocation under sudo."""
    return ["sudo", "-u", linux_user, "python3", HELPER_PATH, *args]


def kill_process_group(proc) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


async def run_file_op(linux_user: str, payload: dict, timeout: int = 30) -> dict:
    """Run a JSON file operation via file_helper.py as linux_user."""
    proc = await asyncio.create_subprocess_exec(
        *helper_cmd(linux_user),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        preexec_fn=os.setsid,
    )
    stdin_data = json.dumps(payload).encode()
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=stdin_data), timeout=timeout
        )
    except asyncio.TimeoutError:
        kill_process_group(proc)
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass
        return {"error": "File operation timed out"}
    if proc.returncode != 0:
        return {"error": stderr.decode(errors="replace").strip() or "Helper failed"}
    return json.loads(stdout.decode())
