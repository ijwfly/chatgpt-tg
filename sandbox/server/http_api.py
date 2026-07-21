"""HTTP routes: health check, bash execution, file operations, files API.

All routes except /health require the X-User-Id header (enforced by
UserIdMiddleware in main.py, which provisions the user and sets the
current_user contextvar).
"""

import asyncio
import json
import os
import time

from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from common import (
    BASH_TIMEOUT_MAX,
    PathOutsideWorkspace,
    get_current_user,
    helper_cmd,
    kill_process_group,
    logger,
    resolve_path,
    run_file_op,
    workspace_for,
)

CHUNK_SIZE = 64 * 1024
STREAM_TIMEOUT = 600  # generous bound so a hung sudo/helper cannot leak forever

FILEOP_ALLOWED = {"read", "write", "edit", "stat", "list", "delete"}


async def health(request):
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Bash execution
# ---------------------------------------------------------------------------

async def exec_command(request):
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    command = body.get("command")
    if not command or not isinstance(command, str):
        return JSONResponse({"error": "command is required"}, status_code=400)
    try:
        timeout = int(body.get("timeout", 30))
    except (TypeError, ValueError):
        return JSONResponse({"error": "timeout must be an integer"}, status_code=400)
    timeout = max(1, min(timeout, BASH_TIMEOUT_MAX))

    linux_user = get_current_user()
    cwd = workspace_for(linux_user)
    cmd = ["sudo", "-u", linux_user, "bash", "-c", command]

    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        preexec_fn=os.setsid,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        logger.info(
            "exec user=%s exit=%s duration=%.2fs command=%r",
            linux_user, proc.returncode, time.monotonic() - started, command[:200],
        )
        return JSONResponse({
            "stdout": stdout.decode(errors="replace"),
            "stderr": stderr.decode(errors="replace"),
            "exit_code": proc.returncode,
            "cwd": cwd,
        })
    except asyncio.TimeoutError:
        # Kill entire process group (sudo + bash + all children)
        kill_process_group(proc)
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass
        logger.info(
            "exec user=%s exit=timeout duration=%.2fs command=%r",
            linux_user, time.monotonic() - started, command[:200],
        )
        return JSONResponse({
            "stdout": "",
            "stderr": f"Command timed out after {timeout} seconds",
            "exit_code": -1,
            "cwd": cwd,
        })


# ---------------------------------------------------------------------------
# JSON file operations (read/write/edit/stat/list/delete)
# ---------------------------------------------------------------------------

async def fileop(request):
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    op = body.get("op")
    if op not in FILEOP_ALLOWED:
        return JSONResponse({"error": f"Unknown op: {op}"}, status_code=400)
    path = body.get("path")
    if not isinstance(path, str):
        return JSONResponse({"error": "path is required"}, status_code=400)

    try:
        resolved, linux_user = resolve_path(path)
    except PathOutsideWorkspace as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    result = await run_file_op(linux_user, {**body, "op": op, "path": resolved})
    logger.info("fileop op=%s user=%s path=%s ok=%s", op, linux_user, resolved,
                "error" not in result)
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Files API (streaming upload/download)
# ---------------------------------------------------------------------------

def _error_status(error: str) -> int:
    lowered = error.lower()
    if "permission denied" in lowered:
        return 403
    if "not found" in lowered:
        return 404
    return 400


def _resolve_or_error(rel_path: str):
    """Returns (abs_path, linux_user, None) or (None, None, error_response)."""
    try:
        abs_path, linux_user = resolve_path(rel_path)
        return abs_path, linux_user, None
    except PathOutsideWorkspace as e:
        return None, None, JSONResponse({"error": str(e)}, status_code=400)


async def files_get(request):
    rel_path = request.path_params.get("path", "")
    abs_path, linux_user, err = _resolve_or_error(rel_path)
    if err:
        return err

    st = await run_file_op(linux_user, {"op": "stat", "path": abs_path})
    if "error" in st:
        return JSONResponse({"error": st["error"]}, status_code=_error_status(st["error"]))
    if st["type"] == "missing":
        return JSONResponse({"error": f"Not found: {rel_path or '/'}"}, status_code=404)

    if st["type"] == "dir":
        result = await run_file_op(linux_user, {"op": "list", "path": abs_path})
        if "error" in result:
            return JSONResponse({"error": result["error"]}, status_code=_error_status(result["error"]))
        logger.info("files list user=%s path=%s", linux_user, abs_path)
        return JSONResponse({"path": rel_path or "/", "entries": result["entries"]})

    return await _stream_download(linux_user, abs_path)


async def _stream_download(linux_user, abs_path):
    proc = await asyncio.create_subprocess_exec(
        *helper_cmd(linux_user, "--stream-read", abs_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        preexec_fn=os.setsid,
    )
    first = await proc.stdout.read(CHUNK_SIZE)
    if not first:
        stderr = (await proc.stderr.read()).decode(errors="replace").strip()
        await proc.wait()
        if proc.returncode != 0:
            return JSONResponse(
                {"error": stderr or "read failed"},
                status_code=_error_status(stderr),
            )

    async def body():
        try:
            if first:
                yield first
            while True:
                chunk = await proc.stdout.read(CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk
            await proc.wait()
        finally:
            if proc.returncode is None:
                kill_process_group(proc)

    filename = os.path.basename(abs_path).replace('"', "")
    logger.info("files download user=%s path=%s", linux_user, abs_path)
    return StreamingResponse(
        body(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def files_put(request):
    rel_path = request.path_params.get("path", "")
    if not rel_path or rel_path.endswith("/"):
        return JSONResponse({"error": "Upload path must name a file"}, status_code=400)
    abs_path, linux_user, err = _resolve_or_error(rel_path)
    if err:
        return err

    proc = await asyncio.create_subprocess_exec(
        *helper_cmd(linux_user, "--stream-write", abs_path),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        preexec_fn=os.setsid,
    )
    try:
        async for chunk in request.stream():
            if chunk:
                proc.stdin.write(chunk)
                await proc.stdin.drain()
        proc.stdin.close()
    except (BrokenPipeError, ConnectionResetError):
        pass  # helper died early — its stderr explains why

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=STREAM_TIMEOUT)
    except asyncio.TimeoutError:
        kill_process_group(proc)
        return JSONResponse({"error": "Upload timed out"}, status_code=500)

    if proc.returncode != 0:
        error = stderr.decode(errors="replace").strip() or "write failed"
        return JSONResponse({"error": error}, status_code=_error_status(error))

    result = json.loads(stdout.decode())
    logger.info("files upload user=%s path=%s size=%s", linux_user, abs_path, result.get("size"))
    return JSONResponse(result)


async def files_delete(request):
    rel_path = request.path_params.get("path", "")
    if not rel_path:
        return JSONResponse({"error": "Delete path must name a file"}, status_code=400)
    abs_path, linux_user, err = _resolve_or_error(rel_path)
    if err:
        return err

    result = await run_file_op(linux_user, {"op": "delete", "path": abs_path})
    if "error" in result:
        return JSONResponse({"error": result["error"]}, status_code=_error_status(result["error"]))
    logger.info("files delete user=%s path=%s", linux_user, abs_path)
    return JSONResponse(result)


def build_routes() -> list:
    return [
        Route("/health", health, methods=["GET"]),
        Route("/exec", exec_command, methods=["POST"]),
        Route("/fileop", fileop, methods=["POST"]),
        Route("/files", files_get, methods=["GET"]),
        Route("/files/{path:path}", files_get, methods=["GET"]),
        Route("/files/{path:path}", files_put, methods=["PUT"]),
        Route("/files/{path:path}", files_delete, methods=["DELETE"]),
    ]
