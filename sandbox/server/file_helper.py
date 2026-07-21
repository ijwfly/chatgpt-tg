#!/usr/bin/env python3
"""Standalone helper for file operations, executed via sudo -u <user>.

JSON protocol: reads a single JSON object from stdin, writes a JSON result
to stdout.

Operations:
  {"op": "read",   "path": "...", "limit": 0}
  {"op": "write",  "path": "...", "content": "..."}
  {"op": "edit",   "path": "...", "old_text": "...", "new_text": "..."}
  {"op": "stat",   "path": "..."}
  {"op": "list",   "path": "..."}
  {"op": "delete", "path": "..."}

Streaming modes (raw bytes, for the files HTTP API):
  file_helper.py --stream-read <path>    file contents to stdout
  file_helper.py --stream-write <path>   stdin to file, JSON result to stdout
"""

import json
import os
import shutil
import sys


def do_read(path: str, limit: int = 0) -> dict:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except FileNotFoundError:
        return {"error": f"File not found: {path}"}
    except IsADirectoryError:
        return {"error": f"Is a directory: {path}"}
    except PermissionError:
        return {"error": f"Permission denied: {path}"}

    lines = content.splitlines(keepends=True)
    if limit > 0:
        lines = lines[:limit]
        content = "".join(lines)

    return {
        "content": content,
        "size": os.path.getsize(path),
        "lines": len(lines),
    }


def do_write(path: str, content: str) -> dict:
    try:
        parent = os.path.dirname(path)
        os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except PermissionError:
        return {"error": f"Permission denied: {path}"}
    except IsADirectoryError:
        return {"error": f"Is a directory: {path}"}

    return {
        "status": "ok",
        "size": os.path.getsize(path),
        "path": path,
    }


def do_edit(path: str, old_text: str, new_text: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except FileNotFoundError:
        return {"error": f"File not found: {path}"}
    except IsADirectoryError:
        return {"error": f"Is a directory: {path}"}
    except PermissionError:
        return {"error": f"Permission denied: {path}"}

    count = content.count(old_text)
    if count == 0:
        return {"error": "old_text not found"}
    if count > 1:
        return {"error": f"old_text found {count} times, must be unique"}

    new_content = content.replace(old_text, new_text, 1)

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
    except PermissionError:
        return {"error": f"Permission denied: {path}"}

    return {"status": "ok", "replacements": 1}


def do_stat(path: str) -> dict:
    try:
        if not os.path.exists(path):
            return {"type": "missing"}
        if os.path.isdir(path):
            return {"type": "dir"}
        return {"type": "file", "size": os.path.getsize(path)}
    except PermissionError:
        return {"error": f"Permission denied: {path}"}
    except OSError as e:
        return {"error": str(e)}


def do_list(path: str) -> dict:
    entries = []
    try:
        with os.scandir(path) as it:
            for e in sorted(it, key=lambda e: e.name):
                try:
                    st = e.stat(follow_symlinks=False)
                    entries.append({
                        "name": e.name,
                        "type": "dir" if e.is_dir(follow_symlinks=False) else "file",
                        "size": st.st_size,
                        "mtime": int(st.st_mtime),
                    })
                except OSError:
                    entries.append({"name": e.name, "type": "unknown"})
    except FileNotFoundError:
        return {"error": f"Directory not found: {path}"}
    except NotADirectoryError:
        return {"error": f"Not a directory: {path}"}
    except PermissionError:
        return {"error": f"Permission denied: {path}"}
    return {"path": path, "entries": entries}


def do_delete(path: str) -> dict:
    try:
        if os.path.isdir(path):
            return {"error": f"Is a directory: {path} (use bash_exec with rm -r)"}
        os.unlink(path)
    except FileNotFoundError:
        return {"error": f"File not found: {path}"}
    except PermissionError:
        return {"error": f"Permission denied: {path}"}
    return {"status": "ok", "deleted": path}


def stream_read(path: str) -> int:
    try:
        with open(path, "rb") as f:
            shutil.copyfileobj(f, sys.stdout.buffer)
        sys.stdout.buffer.flush()
    except OSError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


def stream_write(path: str) -> int:
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "wb") as f:
            shutil.copyfileobj(sys.stdin.buffer, f)
        json.dump({"status": "ok", "size": os.path.getsize(path), "path": path}, sys.stdout)
    except OSError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--stream-read":
        sys.exit(stream_read(sys.argv[2]))
    if len(sys.argv) == 3 and sys.argv[1] == "--stream-write":
        sys.exit(stream_write(sys.argv[2]))

    req = json.loads(sys.stdin.read())
    op = req.get("op")
    if op == "read":
        result = do_read(req["path"], req.get("limit", 0))
    elif op == "write":
        result = do_write(req["path"], req["content"])
    elif op == "edit":
        result = do_edit(req["path"], req["old_text"], req["new_text"])
    elif op == "stat":
        result = do_stat(req["path"])
    elif op == "list":
        result = do_list(req["path"])
    elif op == "delete":
        result = do_delete(req["path"])
    else:
        result = {"error": f"Unknown op: {op}"}
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    main()
