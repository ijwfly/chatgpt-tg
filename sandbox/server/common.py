"""Shared sandbox configuration, identity provisioning and subprocess isolation."""

import asyncio
import json
import logging
import os
import shutil
import stat
import re
import signal
from identities import IdentityError, IdentityRegistry, LINUX_USER_RE, check_root_directory
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
    IdentityRegistry(WORKSPACE_ROOT).ensure_user(linux_user)


def initialize_sandbox() -> None:
    """Restore identities before shared-skill sync or HTTP serving."""
    IdentityRegistry(WORKSPACE_ROOT).restore()
    sync_public_skills()


def _check_public_tree(path: str) -> None:
    st = os.lstat(path)
    if (st.st_uid != 0 or st.st_gid != 0 or st.st_mode & 0o022
            or not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode))):
        raise IdentityError("Unsafe shared skills ownership, type or permissions")
    if stat.S_ISDIR(st.st_mode):
        for name in os.listdir(path):
            _check_public_tree(os.path.join(path, name))


def sync_public_skills(source: str = "/opt/skills") -> None:
    """Sync bundled skills without following volume-controlled symlinks."""
    check_root_directory(WORKSPACE_ROOT)
    try:
        os.mkdir(PUBLIC_SKILLS_DIR, 0o755)
    except FileExistsError:
        pass
    _check_public_tree(PUBLIC_SKILLS_DIR)
    if os.path.isdir(source):
        _check_public_tree(source)
        for name in os.listdir(source):
            bundled = os.path.join(source, name)
            if not os.path.isdir(bundled):
                continue
            destination = os.path.join(PUBLIC_SKILLS_DIR, name)
            if os.path.exists(destination):
                if not os.path.isdir(destination):
                    raise IdentityError("Bundled skill conflicts with a shared file")
                shutil.rmtree(destination)
            shutil.copytree(bundled, destination)
    # Existing admin skills stay intact, readable by users but never writable.
    for base, directories, files in os.walk(PUBLIC_SKILLS_DIR):
        os.chmod(base, 0o755)
        for name in files:
            path = os.path.join(base, name)
            os.chmod(path, 0o755 if os.stat(path).st_mode & 0o111 else 0o644)


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


def user_environment(linux_user: str) -> dict:
    return {
        "HOME": workspace_for(linux_user),
        "USER": linux_user,
        "LOGNAME": linux_user,
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
    }


def user_cmd(linux_user: str, *command: str) -> list:
    # sudo may rewrite PATH; env -i makes the target's exact environment explicit.
    environment = user_environment(linux_user)
    return ["/usr/bin/sudo", "-H", "-u", linux_user, "--", "/usr/bin/env", "-i",
            *(f"{key}={value}" for key, value in environment.items()), *command]


def user_process_options(linux_user: str) -> dict:
    return {
        "env": user_environment(linux_user),
        "cwd": workspace_for(linux_user),
        "start_new_session": True,
        "umask": 0o077,
    }


def bash_cmd(linux_user: str, command: str) -> list:
    return user_cmd(linux_user, "/bin/bash", "--noprofile", "--norc", "-c",
                    'umask 077\nexport PATH="$HOME/.local/bin:$PATH"\n' + command)


def helper_cmd(linux_user: str, *args: str) -> list:
    return user_cmd(linux_user, "/usr/bin/python3", "-I", HELPER_PATH, *args)


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
        **user_process_options(linux_user),
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
