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
  {"op": "skills",  "paths": [{"path": "...", "scope": "..."}, ...]}

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


# --- skills catalog ---------------------------------------------------------

SKILL_FILE = "SKILL.md"
FRONTMATTER_READ_BYTES = 4096  # frontmatter sits at the top; the body is never loaded here
MAX_SKILL_DIRS = 200


def parse_frontmatter(text: str) -> dict:
    """Parse the leading `---` block of a SKILL.md into a dict of simple key: value pairs.

    Raises ValueError when the block is missing or unterminated.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError('missing frontmatter: SKILL.md must start with "---"')
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        raise ValueError('frontmatter is not closed with a "---" line')

    fields = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        fields[key.strip()] = value
    return fields


def scan_skills_dir(path: str, scope: str) -> tuple:
    """Returns (skills, invalid) for one skills root. A missing root is not an error."""
    skills, invalid = [], []
    try:
        entries = sorted(os.scandir(path), key=lambda e: e.name)
    except (FileNotFoundError, NotADirectoryError):
        return skills, invalid
    except PermissionError:
        return skills, [{"dir": path, "scope": scope, "error": "Permission denied"}]

    for entry in entries[:MAX_SKILL_DIRS]:
        if not entry.is_dir(follow_symlinks=False) or entry.name.startswith("."):
            continue
        skill_md = os.path.join(entry.path, SKILL_FILE)
        try:
            with open(skill_md, "r", encoding="utf-8", errors="replace") as f:
                head = f.read(FRONTMATTER_READ_BYTES)
        except FileNotFoundError:
            invalid.append({"dir": entry.name, "scope": scope, "error": "no SKILL.md"})
            continue
        except (OSError, IsADirectoryError) as e:
            invalid.append({"dir": entry.name, "scope": scope, "error": str(e)})
            continue

        try:
            fields = parse_frontmatter(head)
        except ValueError as e:
            invalid.append({"dir": entry.name, "scope": scope, "error": str(e)})
            continue

        name = fields.get("name") or entry.name
        description = fields.get("description", "")
        if not description:
            invalid.append({"dir": entry.name, "scope": scope, "error": "no description"})
            continue

        skills.append({
            "name": name,
            "description": description,
            "scope": scope,
            "dir": entry.path,
            "skill_md": skill_md,
        })
    return skills, invalid


def do_skills(paths: list) -> dict:
    skills, invalid = [], []
    for item in paths:
        found, bad = scan_skills_dir(item["path"], item.get("scope", "personal"))
        skills.extend(found)
        invalid.extend(bad)
    return {"skills": skills, "invalid": invalid}


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
    elif op == "skills":
        result = do_skills(req.get("paths", []))
    else:
        result = {"error": f"Unknown op: {op}"}
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    main()
