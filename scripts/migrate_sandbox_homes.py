#!/usr/bin/env python3
"""Offline, non-destructive migration of legacy sandbox homes (stdlib only).

Commands (stop the bot, then `docker stop OLD` before export):
  python3 scripts/migrate_sandbox_homes.py plan --container OLD
  python3 scripts/migrate_sandbox_homes.py export --container OLD --backup /absolute/backup
  python3 scripts/migrate_sandbox_homes.py apply --container OLD --backup /absolute/backup --image NEW_SANDBOX_IMAGE
  python3 scripts/migrate_sandbox_homes.py verify --container OLD --backup /absolute/backup --image NEW_SANDBOX_IMAGE

For explicitly resolved identities, add --identity-map /absolute/identities.json
on export. Format: {"user_123":{"uid":1001,"gid":1001,
"workspace_uid":1002,"workspace_gid":1002}}. uid/gid describe the saved
Linux identity, not a new allocation; workspace_uid/gid explicitly authorize
translation of that workspace's previous ownership. Volume-only users require
uid/gid. Export freezes this resolution into the manifest. Unknown ownership
fails closed. Never delete the old container until export AND verify succeed.

The old container must remain stopped throughout export/apply/verify. Helpers
mount its volume offline; they never start its server, restore passwd wholesale,
rebuild it, remove it, or create /home compatibility links. Absolute /home
references are reported, not rewritten; recreate relocated virtualenvs manually.
--image selects the new sandbox image for offline UID/GID conflict checking;
if omitted, the exported legacy image is used. Both require stdlib python3.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

VERSION = 1
USER_RE = re.compile(r"^user_[a-z0-9_]{1,27}$")
LAYOUT = ("inbox", "projects", "outbox", "tmp", "skills")
ARTIFACTS = ("workspace.tar", "home.tar", "passwd", "group")


class MigrationError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise MigrationError(message)


def digest_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_relative(value):
    p = PurePosixPath(value)
    require(not p.is_absolute() and ".." not in p.parts, f"Unsafe archive path: {value!r}")
    return str(p)


def read_archive(path, root):
    """Inventory without extracting, following links, or trusting tar names."""
    result = {}
    with tarfile.open(path, "r:*") as archive:
        for member in archive:
            name = safe_relative(member.name)
            require(name == root or name.startswith(root + "/"), f"Unexpected archive root: {name}")
            relative = name[len(root):].lstrip("/")
            require(relative not in result, f"Duplicate archive member: {name}")
            kind = "dir" if member.isdir() else "file" if member.isfile() else "link" if member.issym() else "unsupported"
            record = {"kind": kind, "mode": member.mode, "uid": member.uid, "gid": member.gid}
            if kind == "file":
                stream = archive.extractfile(member)
                h = hashlib.sha256()
                home_reference = False
                previous = b""
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(chunk)
                    home_reference |= b"/home/" in previous + chunk
                    previous = chunk[-6:]
                record.update(sha256=h.hexdigest(), size=member.size, home_reference=home_reference)
            if kind == "link":
                record["target"] = member.linkname
            result[relative] = record
    require("" in result and result[""]["kind"] == "dir", f"Missing {root} directory")
    return result


def account_rows(text, group=False):
    rows = {}
    for line in text.splitlines():
        fields = line.split(":")
        if not line or line.startswith("#"):
            continue
        require(len(fields) == (4 if group else 7), "Malformed account snapshot")
        name = fields[0]
        require(name not in rows, f"Duplicate account: {name}")
        try:
            rows[name] = {"gid": int(fields[2])} if group else {
                "uid": int(fields[2]), "gid": int(fields[3]), "home": fields[5], "shell": fields[6]}
        except ValueError as exc:
            raise MigrationError("Malformed account ID") from exc
    return rows


def validate_link(path, record, user):
    target = record["target"]
    if target.startswith("/"):
        require(target == f"/workspace/{user}" or target.startswith(f"/workspace/{user}/"),
                f"Escaping/old-home absolute symlink: {user}/{path}")
        parts = PurePosixPath(target).parts[3:]
    else:
        parts = PurePosixPath(path).parent.parts + PurePosixPath(target).parts
    depth = 0
    for part in parts:
        depth += -1 if part == ".." else 0 if part in (".", "/") else 1
        require(depth >= 0, f"Escaping symlink: {user}/{path}")


def build_plan(workspace, homes, passwd, groups, mapping):
    names = set(name for name in passwd if USER_RE.fullmatch(name))
    conflicts = []
    root = workspace.get("", {})
    if root.get("kind") != "dir" or root.get("uid") != 0 or root.get("gid") != 0 or root.get("mode", 0) & 0o022:
        conflicts.append("Workspace root must be root:root and not group/world writable")
    for user in passwd:
        if user.startswith("user_") and not USER_RE.fullmatch(user):
            conflicts.append(f"Invalid passwd username: {user}")
    for label, inventory in (("workspace", workspace), ("home", homes)):
        for path, record in inventory.items():
            if record["kind"] not in ("dir", "file", "link") or record["mode"] & 0o6000:
                conflicts.append(f"Special/hardlink/privileged backup entry: {label}/{path}")
    for path in workspace:
        top = path.split("/")[0]
        if top.startswith("user_"):
            if not USER_RE.fullmatch(top):
                conflicts.append(f"Invalid workspace username: {top}")
            else:
                names.add(top)
    for path in homes:
        top = path.split("/")[0]
        if top.startswith("user_"):
            if USER_RE.fullmatch(top):
                names.add(top)
            else:
                conflicts.append(f"Invalid home username: {top}")
    require(isinstance(mapping, dict), "Identity map must be an object")
    require(set(mapping) <= names, "Identity map contains unknown users")
    identities, merged, sources = {}, {}, {}
    used_uid, used_gid = {}, {}
    for user in sorted(names):
        try:
            override = mapping.get(user, {})
            require(isinstance(override, dict) and set(override) <= {"uid", "gid", "workspace_uid", "workspace_gid"},
                    f"Invalid identity map for {user}")
            old = passwd.get(user)
            uid, gid = override.get("uid", old and old["uid"]), override.get("gid", old and old["gid"])
            require(type(uid) is int and 0 < uid < 2**32 - 1 and type(gid) is int and 0 < gid < 2**32 - 1,
                    f"{user}: missing/invalid positive Linux UID/GID; supply --identity-map")
            if old:
                require((uid, gid) == (old["uid"], old["gid"]), f"{user}: identity remapping is not supported")
                require(old["home"] in (f"/home/{user}", f"/workspace/{user}"), f"{user}: unsupported passwd home {old['home']}")
            for other, row in passwd.items():
                require(row["uid"] != uid or other == user, f"UID {uid} belongs to {other}, not {user}")
            for other, row in groups.items():
                require(row["gid"] != gid or other == user, f"GID {gid} belongs to {other}, not {user}")
            require(uid not in used_uid and gid not in used_gid, f"{user}: duplicate UID/GID")
            used_uid[uid], used_gid[gid] = user, user
            identities[user] = {"uid": uid, "gid": gid}
            prefix = user + "/"
            combined = {}
            for label, inventory in (("workspace", workspace), ("home", homes)):
                root = inventory.get(user)
                if root:
                    require(root["kind"] == "dir", f"{label}/{user}: home root is not a directory")
                for path, record in sorted(inventory.items()):
                    if path != user and not path.startswith(prefix):
                        continue
                    relative = path[len(prefix):] if path != user else ""
                    require(record["kind"] in ("dir", "file", "link"), f"Special/hardlink file: {label}/{path}")
                    require(not record["mode"] & 0o6000, f"Privileged file mode: {label}/{path}")
                    source_uid = override.get("workspace_uid", uid) if label == "workspace" else uid
                    source_gid = override.get("workspace_gid", gid) if label == "workspace" else gid
                    require(type(source_uid) is int and 0 <= source_uid < 2**32 - 1
                            and type(source_gid) is int and 0 <= source_gid < 2**32 - 1,
                            f"{user}: invalid ownership mapping")
                    require(record["uid"] == source_uid and record["gid"] == source_gid,
                            f"Owner mismatch: {label}/{path} is {record['uid']}:{record['gid']}, expected {source_uid}:{source_gid}")
                    if record["kind"] == "link":
                        validate_link(relative, record, user)
                    normalized = dict(record, uid=uid, gid=gid)
                    if relative == "":
                        normalized["mode"] = 0o700
                    if relative in combined:
                        prior = combined[relative]
                        require(prior["kind"] == normalized["kind"], f"Type conflict: {user}/{relative}")
                        if prior["kind"] != "dir":
                            require(prior == normalized, f"Content/metadata conflict: {user}/{relative}")
                    else:
                        combined[relative] = normalized
                        sources[f"{user}/{relative}".rstrip("/")] = [label, path]
            combined.setdefault("", {"kind": "dir", "mode": 0o700, "uid": uid, "gid": gid})
            for directory in LAYOUT:
                require(directory not in combined or combined[directory]["kind"] == "dir", f"Layout conflict: {user}/{directory}")
                combined[directory] = {"kind": "dir", "mode": 0o700, "uid": uid, "gid": gid}
            # No child may be hidden behind a symlink or a regular file.
            for relative in combined:
                for parent in PurePosixPath(relative).parents:
                    if str(parent) == ".":
                        continue
                    require(str(parent) in combined and combined[str(parent)]["kind"] == "dir", f"Unsafe ancestor: {user}/{relative}")
            merged[user] = combined
        except MigrationError as exc:
            conflicts.append(str(exc))
    if ".sandbox" in workspace:
        conflicts.append("Existing /workspace/.sandbox metadata: use original backup to retry, not a new export")
    references = sorted(f"{label}/{path}" for label, inventory in (("workspace", workspace), ("home", homes))
                        for path, row in inventory.items() if row.get("home_reference") or path.endswith("pyvenv.cfg"))
    return {"users": identities, "merged": merged, "sources": sources, "conflicts": conflicts,
            "relocation_caveats": references, "workspace": workspace, "homes": homes}


def docker(*args, output=None):
    result = subprocess.run(["docker", *args], stdout=output if output else subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    require(result.returncode == 0, f"Docker command failed ({args[0]}): {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def container_info(container, stopped=False):
    raw = json.loads(docker("inspect", container))[0]
    require(not stopped or not raw["State"]["Running"], "Stop the bot/in-flight tasks and `docker stop` the old container first")
    mounts = [m for m in raw["Mounts"] if m["Destination"] == "/workspace"]
    require(len(mounts) == 1 and mounts[0]["Type"] in ("volume", "bind"), "Expected one persistent /workspace mount")
    require(not any(m["Destination"].startswith("/workspace/") or m["Destination"] == "/home" or
                    m["Destination"].startswith("/home/") for m in raw["Mounts"]), "Nested/home mounts require a separate operator migration")
    mount = mounts[0]
    return {"id": raw["Id"], "image": raw["Image"], "running": raw["State"]["Running"],
            "workspace_mount": {k: mount[k] for k in ("Type", "Source", "Destination")}}


def require_offline_volume(info):
    running = docker("ps", "-q").decode().split()
    if not running:
        return
    source = info["workspace_mount"]["Source"].rstrip("/")
    for container_id in running:
        try:
            containers = json.loads(docker("inspect", container_id))
        except MigrationError as exc:
            # An unrelated disposable container can exit between ps and inspect.
            if "no such object:" in str(exc).lower():
                continue
            raise
        for container in containers:
            for mount in container["Mounts"]:
                other = mount.get("Source", "").rstrip("/")
                overlap = other and (other == source or other.startswith(source + "/") or source.startswith(other + "/"))
                require(not overlap, f"Stop container {container['Id'][:12]}: it also mounts the workspace volume")


def private_directory(path, new=False):
    path = Path(path)
    require(path.is_absolute(), "Backup path must be absolute")
    require(all(not parent.is_symlink() for parent in (path, *path.parents)), "Backup path must not contain symlinks")
    if new:
        path.mkdir(mode=0o700, parents=False, exist_ok=False)
    require(path.is_dir() and stat.S_IMODE(path.stat().st_mode) == 0o700, "Backup directory must have mode 0700")
    return path


def save_json(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def capture(container, destination, mapping):
    for remote, name in (("/workspace", "workspace.tar"), ("/home", "home.tar"), ("/etc/passwd", "passwd.tar"), ("/etc/group", "group.tar")):
        fd = os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            docker("cp", f"{container}:{remote}", "-", output=stream)
    for name in ("passwd", "group"):
        with tarfile.open(destination / f"{name}.tar") as archive:
            members = archive.getmembers()
            require(len(members) == 1 and members[0].isfile(), f"Unsafe /etc/{name}")
            data = archive.extractfile(members[0]).read()
            fd = os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
        (destination / f"{name}.tar").unlink()
    workspace = read_archive(destination / "workspace.tar", "workspace")
    homes = read_archive(destination / "home.tar", "home")
    passwd = account_rows((destination / "passwd").read_text())
    groups = account_rows((destination / "group").read_text(), group=True)
    return build_plan(workspace, homes, passwd, groups, mapping)


def load_backup(path):
    path = private_directory(path)
    manifest_file = path / "manifest.json"
    require(manifest_file.is_file() and not manifest_file.is_symlink(), "Missing safe manifest.json: export has not completed")
    require(stat.S_IMODE(manifest_file.stat().st_mode) == 0o600, "Manifest must have mode 0600")
    manifest = json.loads(manifest_file.read_text())
    require(manifest.get("version") == VERSION, "Unsupported backup version")
    for name in ARTIFACTS:
        file = path / name
        require(file.is_file() and not file.is_symlink() and stat.S_IMODE(file.stat().st_mode) == 0o600, f"Unsafe backup artifact: {name}")
        require(digest_file(file) == manifest["checksums"][name], f"Backup checksum mismatch: {name}")
    plan = capture_plan_from_backup(path, manifest["identity_map"])
    require(not plan["conflicts"], "Backup preflight conflicts: " + "; ".join(plan["conflicts"]))
    require(plan == manifest["plan"], "Backup inventory/manifest mismatch")
    return manifest


def capture_plan_from_backup(path, mapping):
    return build_plan(read_archive(path / "workspace.tar", "workspace"), read_archive(path / "home.tar", "home"),
                      account_rows((path / "passwd").read_text()), account_rows((path / "group").read_text(), True), mapping)


def filesystem_inventory(root):
    require(root.is_dir() and not root.is_symlink(), f"Unsafe root: {root}")
    result = {}
    def visit(path, relative):
        info = path.lstat()
        kind = "dir" if stat.S_ISDIR(info.st_mode) else "file" if stat.S_ISREG(info.st_mode) else "link" if stat.S_ISLNK(info.st_mode) else "unsupported"
        row = {"kind": kind, "mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid}
        if kind == "file":
            require(info.st_nlink == 1, f"Hardlinked file requires explicit operator resolution: {relative}")
            row.update(sha256=digest_file(path), size=info.st_size)
        if kind == "link":
            row["target"] = os.readlink(path)
        result[relative] = row
        if kind == "dir":
            for child in sorted(path.iterdir()):
                visit(child, f"{relative}/{child.name}".lstrip("/"))
    visit(root, "")
    return result


def comparable(record):
    return {key: value for key, value in record.items() if key != "home_reference"}


def target_inventory(plan):
    target = {path: comparable(row) for path, row in plan["workspace"].items()
              if path.split("/")[0] not in plan["users"]}
    for user, entries in plan["merged"].items():
        for path, row in entries.items():
            target[f"{user}/{path}".rstrip("/")] = comparable(row)
    return target


def check_account_conflicts(users, passwd_text, group_text):
    passwd, groups = account_rows(passwd_text), account_rows(group_text, True)
    require("mcpusers" in groups and groups["mcpusers"]["gid"] > 0, "Target image must provide a nonroot mcpusers group")
    for name in set(passwd) | set(groups):
        require(not name.startswith("user_") or name in users, f"Unregistered target image account/group: {name}")
    for user in users:
        if user in passwd:
            require(passwd[user]["home"] == f"/workspace/{user}" and passwd[user]["shell"] == "/bin/bash",
                    f"Target image has an incompatible pre-existing account: {user}")
    for user, identity in users.items():
        for name, row in passwd.items():
            require((name != user or row["uid"] == identity["uid"] and row["gid"] == identity["gid"]) and
                    (row["uid"] != identity["uid"] or name == user), f"Target image UID conflict: {user}/{name}")
        for name, row in groups.items():
            require((name != user or row["gid"] == identity["gid"]) and
                    (row["gid"] != identity["gid"] or name == user), f"Target image GID conflict: {user}/{name}")


def atomic_metadata(metadata, name, value):
    temporary = metadata / "pending-json"
    if temporary.exists() or temporary.is_symlink():
        require(temporary.is_file() and not temporary.is_symlink() and temporary.stat().st_uid == 0
                and temporary.stat().st_nlink == 1, "Unsafe interrupted metadata write")
        temporary.unlink()
    save_json(temporary, value)
    os.replace(temporary, metadata / name)


def validate_metadata(root, manifest, create=False):
    metadata = root / ".sandbox"
    fingerprint = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    marker = {"version": VERSION, "backup_sha256": fingerprint}
    if metadata.exists() or metadata.is_symlink():
        require(metadata.is_dir() and not metadata.is_symlink(), "Unsafe registry directory")
        info = metadata.stat()
        require(info.st_uid == 0 and info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o700, "Unsafe registry directory ownership/mode")
        names = set(p.name for p in metadata.iterdir())
        require(names <= {"migration.json", "users.json", "pending-data", "pending-json", "users.lock"}, "Unexpected migration metadata")
        marker_file = metadata / "migration.json"
        if marker_file.exists() or marker_file.is_symlink():
            info = marker_file.lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600
                    and info.st_uid == 0 and info.st_gid == 0, "Unsafe migration marker")
            require(json.loads(marker_file.read_text()) == marker, "Different/partial migration: use the original backup")
        else:
            require(names <= {"pending-json"}, "Missing migration marker")
            if create:
                atomic_metadata(metadata, "migration.json", marker)
        for name in ("users.json", "users.lock"):
            path = metadata / name
            if path.exists() or path.is_symlink():
                info = path.lstat()
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == 0 and info.st_gid == 0
                        and stat.S_IMODE(info.st_mode) == 0o600, f"Unsafe {name}")
                if name == "users.json":
                    require(json.loads(path.read_text()) == {"version": VERSION, "users": manifest["plan"]["users"]}, "Conflicting registry")
    elif create:
        metadata.mkdir(mode=0o700)
        os.chown(metadata, 0, 0)
        atomic_metadata(metadata, "migration.json", marker)
    return metadata


def preflight_current(root, manifest, verify=False):
    plan = manifest["plan"]
    original = {path: comparable(row) for path, row in plan["workspace"].items()}
    info = root.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_gid == 0
            and not stat.S_IMODE(info.st_mode) & 0o022, "Workspace root must be root:root and not group/world writable")
    target = target_inventory(plan)
    current = filesystem_inventory(root)
    validate_metadata(root, manifest)
    for path, row in current.items():
        if path == ".sandbox" or path.startswith(".sandbox/"):
            continue
        require(path in target, f"Unexpected current path: {path}")
        if verify:
            require(row == target[path], f"Target integrity mismatch: {path}")
        else:
            prior = original.get(path, target[path])
            stable = {key: value for key, value in row.items() if key not in ("uid", "gid", "mode")}
            expected = {key: value for key, value in target[path].items() if key not in ("uid", "gid", "mode")}
            require(stable == expected and all(row[key] in (prior[key], target[path][key]) for key in ("uid", "gid", "mode")),
                    f"Changed/conflicting current path: {path}")
    required = target if verify else original
    for path in required:
        require(path in current, f"Missing current path: {path}")
    # The entire preflight completes before any mutation, including permissions.
    return current, target


def install_target(root, backup, manifest):
    current, target = preflight_current(root, manifest)
    metadata = validate_metadata(root, manifest, create=True)
    pending_json = metadata / "pending-json"
    if pending_json.exists() or pending_json.is_symlink():
        info = pending_json.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_gid == 0
                and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600, "Unsafe interrupted metadata write")
        pending_json.unlink()
    pending = metadata / "pending-data"
    if pending.exists() or pending.is_symlink():
        info = pending.lstat()
        require(stat.S_ISLNK(info.st_mode) or stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                or stat.S_ISDIR(info.st_mode) and not any(pending.iterdir()), "Unsafe interrupted data staging")
        if stat.S_ISDIR(info.st_mode):
            pending.rmdir()
        else:
            pending.unlink()
    archives = {label: tarfile.open(backup / f"{label}.tar") for label in ("workspace", "home")}
    members = {label: {safe_relative(member.name): member for member in archive.getmembers()}
               for label, archive in archives.items()}
    try:
        for relative, row in sorted(target.items(), key=lambda item: (item[0].count("/"), item[0])):
            path = root / relative
            if relative not in current:
                if row["kind"] == "dir":
                    pending.mkdir(mode=0o700)
                elif row["kind"] == "link":
                    os.symlink(row["target"], pending)
                else:
                    label, source = manifest["plan"]["sources"][relative]
                    stream = archives[label].extractfile(members[label][f"{label}/{source}"])
                    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    with os.fdopen(fd, "wb") as output:
                        shutil.copyfileobj(stream, output)
                        output.flush()
                        os.fsync(output.fileno())
                os.chown(pending, row["uid"], row["gid"], follow_symlinks=False)
                if row["kind"] != "link":
                    os.chmod(pending, row["mode"])
                os.rename(pending, path)
            elif current[relative] != row:
                os.chown(path, row["uid"], row["gid"], follow_symlinks=False)
                if row["kind"] != "link":
                    os.chmod(path, row["mode"])
        registry_path = metadata / "users.json"
        if not registry_path.exists():
            atomic_metadata(metadata, "users.json", {"version": VERSION, "users": manifest["plan"]["users"]})
        os.chmod(registry_path, 0o600)
    finally:
        for archive in archives.values():
            archive.close()
    verify_target(root, manifest)


def verify_target(root, manifest):
    preflight_current(root, manifest, verify=True)
    metadata = validate_metadata(root, manifest)
    require((metadata / "migration.json").is_file(), "Missing migration marker")
    require(not any((metadata / name).exists() or (metadata / name).is_symlink() for name in ("pending-data", "pending-json")),
            "Interrupted staging remains; retry apply")
    path = metadata / "users.json"
    require(path.is_file() and not path.is_symlink(), "Missing/symlink registry")
    info = path.stat()
    require(info.st_uid == 0 and info.st_gid == 0 and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600, "Unsafe registry ownership/mode")
    require(json.loads(path.read_text()) == {"version": VERSION, "users": manifest["plan"]["users"]}, "Registry integrity mismatch")


def run_helper(container, info, backup, command, image=None):
    # The selected image provides Python only. No entrypoint, network, server,
    # token, shell profiles or user-controlled Python imports are involved.
    image = image or info["image"]
    require(not image.startswith("-") and not any(c.isspace() for c in image), "Invalid helper image")
    # Docker's unique container name serializes helpers without writing a lock
    # into production data before conflict preflight has succeeded.
    lock_name = "sandbox-home-migration-" + hashlib.sha256(info["workspace_mount"]["Source"].encode()).hexdigest()[:24]
    args = ["docker", "run", "--rm", "--name", lock_name, "--network", "none", "--read-only", "--user", "0:0",
            "--cap-drop", "ALL", "--cap-add", "CHOWN", "--cap-add", "FOWNER", "--cap-add", "DAC_OVERRIDE",
            "--tmpfs", "/tmp:rw,nosuid,nodev", "--volumes-from", f"{container}:rw" if command == "apply" else f"{container}:ro",
            "--mount", f"type=bind,src={backup},dst=/migration,readonly", "--entrypoint", "python3", "-i",
            image, "-I", "-", "--worker", command, "--backup", "/migration"]
    # -i belongs to docker run, not Python: it keeps the streamed script stdin open.
    result = subprocess.run(args, input=Path(__file__).read_bytes(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    require(result.returncode == 0, "Offline migration helper failed: " + result.stderr.decode(errors="replace").strip())
    print(result.stdout.decode().strip())


def parser():
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("command", choices=("plan", "export", "apply", "verify"))
    cli.add_argument("--container", help="Existing legacy container name/ID; never automatically removed")
    cli.add_argument("--backup", help="Explicit absolute host backup directory (new for export, existing for apply/verify)")
    cli.add_argument("--identity-map", help="Explicit JSON identity/old-workspace-owner resolution (plan/export only)")
    cli.add_argument("--image", help="New sandbox image for offline apply/verify (default: exported old image)")
    cli.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    os.umask(0o077)
    os.environ.pop("SANDBOX_API_TOKEN", None)
    try:
        if args.worker:
            require(args.command in ("apply", "verify"), "Invalid worker command")
            backup = Path(args.backup)
            manifest = load_backup(backup)
            require(os.geteuid() == 0, "Offline worker requires root")
            check_account_conflicts(manifest["plan"]["users"], Path("/etc/passwd").read_text(), Path("/etc/group").read_text())
            (install_target(Path("/workspace"), backup, manifest) if args.command == "apply" else verify_target(Path("/workspace"), manifest))
            print(f"{args.command}: checksum/path/mode/ownership/registry integrity verified")
            return 0
        require(args.container, "--container is required")
        info = container_info(args.container, stopped=args.command != "plan")
        if args.command != "plan":
            require_offline_volume(info)
        if args.command in ("plan", "export"):
            require(not args.image, "--image is only accepted with apply/verify")
            mapping = json.loads(Path(args.identity_map).read_text()) if args.identity_map else {}
            if args.command == "export":
                require(args.backup, "--backup is required")
                destination = private_directory(args.backup, new=True)
                plan = capture(args.container, destination, mapping)
                require_offline_volume(info)
                require(container_info(args.container, stopped=True) == info, "Container changed during export")
                manifest = {"version": VERSION, "container": info, "identity_map": mapping,
                            "checksums": {name: digest_file(destination / name) for name in ARTIFACTS}, "plan": plan}
                save_json(destination / "manifest.json", manifest)
                print(json.dumps({"backup": str(destination), "users": plan["users"], "conflicts": plan["conflicts"],
                                  "relocation_caveats": plan["relocation_caveats"]}, indent=2))
                require(not plan["conflicts"], "Backup preserved; resolve conflicts and export to a NEW directory before applying")
            else:
                with tempfile.TemporaryDirectory(prefix="sandbox-migration-") as temporary:
                    plan = capture(args.container, Path(temporary), mapping)
                print(json.dumps({"running": info["running"], "users": plan["users"], "conflicts": plan["conflicts"],
                                  "owner_inventory": {label: {p: {k: r[k] for k in ("uid", "gid", "kind")} for p, r in plan[label].items()}
                                                      for label in ("workspace", "homes")},
                                  "relocation_caveats": plan["relocation_caveats"]}, indent=2))
                require(not plan["conflicts"], "Plan has conflicts; no production files were modified")
        else:
            require(args.backup and not args.identity_map, "--backup required; identity mapping is frozen at export")
            backup = private_directory(args.backup)
            manifest = load_backup(backup)
            require(info == manifest["container"], "Container/image/workspace differs from the exported source")
            run_helper(args.container, info, backup, args.command, args.image)
            require(container_info(args.container, stopped=True) == info, "Old container was started/changed during migration")
            require_offline_volume(info)
        return 0
    except (MigrationError, OSError, ValueError, KeyError, tarfile.TarError) as exc:
        print(f"Migration refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
