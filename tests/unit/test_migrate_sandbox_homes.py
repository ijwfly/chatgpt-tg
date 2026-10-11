"""Behavioral regressions for the offline sandbox migration operator."""

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tarfile

import pytest

SPEC = importlib.util.spec_from_file_location(
    "migrate_sandbox_homes", Path(__file__).resolve().parents[2] / "scripts/migrate_sandbox_homes.py"
)
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)
UID = 27123
GID = 27123
ROOT_ONLY = pytest.mark.skipif(os.geteuid() != 0, reason="Actual ownership migration requires Linux root")
PASSWD = f"root:x:0:0:root:/root:/bin/bash\nuser_123:x:{UID}:{GID}::/home/user_123:/bin/bash\n"
GROUP = f"root:x:0:\nuser_123:x:{GID}:\nmcpusers:x:998:\n"


def row(kind="dir", mode=0o700, uid=UID, gid=GID, data=None, target=None):
    result = {"kind": kind, "mode": mode, "uid": uid, "gid": gid}
    if kind == "file":
        result.update(sha256=hashlib.sha256(data).hexdigest(), size=len(data), home_reference=b"/home/" in data)
    if kind == "link":
        result["target"] = target
    return result


def inventories():
    return (
        {"": row(mode=0o755, uid=0, gid=0), "user_123": row(),
         "user_123/root-work.txt": row("file", 0o600, data=b"workspace work")},
        {"": row(mode=0o755, uid=0, gid=0), "user_123": row(),
         "user_123/.secret": row("file", 0o600, data=b"private token contents")},
    )


def plan(workspace=None, homes=None, mapping=None, passwd=PASSWD, group=GROUP):
    defaults = inventories()
    return migration.build_plan(workspace if workspace is not None else defaults[0],
                                homes if homes is not None else defaults[1],
                                migration.account_rows(passwd), migration.account_rows(group, True), mapping or {})


def archive(path, root, entries, payloads):
    with tarfile.open(path, "w") as output:
        for name, record in entries.items():
            info = tarfile.TarInfo(f"{root}/{name}".rstrip("/"))
            info.uid, info.gid, info.mode = record["uid"], record["gid"], record["mode"]
            if record["kind"] == "dir":
                info.type = tarfile.DIRTYPE
                output.addfile(info)
            elif record["kind"] == "link":
                info.type = tarfile.SYMTYPE
                info.linkname = record["target"]
                output.addfile(info)
            else:
                data = payloads[name]
                info.size = len(data)
                output.addfile(info, io.BytesIO(data))
    path.chmod(0o600)


@pytest.fixture
def backup(tmp_path):
    directory = tmp_path / "backup"
    directory.mkdir(mode=0o700)
    workspace, homes = inventories()
    archive(directory / "workspace.tar", "workspace", workspace, {"user_123/root-work.txt": b"workspace work"})
    archive(directory / "home.tar", "home", homes, {"user_123/.secret": b"private token contents"})
    for name, text in (("passwd", PASSWD), ("group", GROUP)):
        (directory / name).write_text(text)
        (directory / name).chmod(0o600)
    manifest = {"version": 1, "identity_map": {}, "container": {"id": "source"},
                "checksums": {name: migration.digest_file(directory / name) for name in migration.ARTIFACTS},
                "plan": migration.capture_plan_from_backup(directory, {})}
    migration.save_json(directory / "manifest.json", manifest)
    return directory, manifest


def original_workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir(mode=0o755)
    user = root / "user_123"
    user.mkdir(mode=0o700)
    (user / "root-work.txt").write_bytes(b"workspace work")
    (user / "root-work.txt").chmod(0o600)
    os.chown(user, UID, GID)
    os.chown(user / "root-work.txt", UID, GID)
    return root


def test_merge_preserves_hidden_files_and_workspace_root_work():
    result = plan()
    assert result["conflicts"] == []
    assert set(result["merged"]["user_123"]) == {"", "root-work.txt", ".secret", *migration.LAYOUT}
    assert result["users"] == {"user_123": {"uid": UID, "gid": GID}}
    assert all(result["merged"]["user_123"][name]["mode"] == 0o700 for name in migration.LAYOUT)


def test_identical_file_is_noop_but_different_content_or_type_conflicts():
    workspace, homes = inventories()
    homes["user_123/root-work.txt"] = dict(workspace["user_123/root-work.txt"])
    assert plan(workspace, homes)["conflicts"] == []
    homes["user_123/root-work.txt"] = row("file", 0o600, data=b"other")
    assert any("Content/metadata conflict" in error for error in plan(workspace, homes)["conflicts"])
    homes["user_123/root-work.txt"] = row()
    assert any("Type conflict" in error for error in plan(workspace, homes)["conflicts"])


@pytest.mark.parametrize("target", ["../../etc/passwd", "/etc/passwd", "/home/user_123/.secret", "../user_other/secret"])
def test_escaping_symlinks_are_rejected(target):
    workspace, homes = inventories()
    homes["user_123/link"] = row("link", 0o777, target=target)
    assert any("symlink" in error for error in plan(workspace, homes)["conflicts"])


def test_safe_internal_symlinks_are_preserved_without_dereference():
    workspace, homes = inventories()
    homes["user_123/link"] = row("link", 0o777, target=".secret")
    result = plan(workspace, homes)
    assert not result["conflicts"]
    assert result["merged"]["user_123"]["link"]["target"] == ".secret"


@pytest.mark.parametrize("record", [row("unsupported"), row("file", 0o4600, data=b"privileged")])
def test_special_files_and_setuid_fail_preflight(record):
    workspace, homes = inventories()
    homes["user_123/danger"] = record
    assert plan(workspace, homes)["conflicts"]


def test_volume_only_user_and_owner_mismatches_require_explicit_resolution():
    workspace, homes = inventories()
    no_user_passwd = "root:x:0:0::/root:/bin/bash\n"
    assert plan(workspace, homes, passwd=no_user_passwd)["conflicts"]
    assert not plan(workspace, homes, {"user_123": {"uid": UID, "gid": GID}}, passwd=no_user_passwd)["conflicts"]
    workspace["user_123"]["uid"] = 27124
    workspace["user_123/root-work.txt"]["uid"] = 27124
    assert plan(workspace, homes)["conflicts"]
    assert not plan(workspace, homes, {"user_123": {"workspace_uid": 27124}})["conflicts"]
    workspace["user_123/root-work.txt"]["uid"] = 54321
    assert plan(workspace, homes, {"user_123": {"workspace_uid": 27124}})["conflicts"]


def test_uid_gid_conflicts_and_identity_remapping_fail():
    assert plan(passwd=PASSWD + f"foreign:x:{UID}:999::/foreign:/bin/bash\n")["conflicts"]
    assert plan(group=GROUP + f"foreign:x:{GID}:\n")["conflicts"]
    assert plan(mapping={"user_123": {"uid": UID + 1, "gid": GID}})["conflicts"]


def test_relocation_caveats_report_paths_not_secret_contents():
    workspace, homes = inventories()
    homes["user_123/.profile"] = row("file", 0o600, data=b"secret-token /home/user_123/bin")
    homes["user_123/pyvenv.cfg"] = row("file", 0o600, data=b"venv")
    result = plan(workspace, homes)
    assert result["relocation_caveats"] == ["home/user_123/.profile", "home/user_123/pyvenv.cfg"]
    assert "secret-token" not in json.dumps(result)


def test_archive_traversal_and_duplicate_members_never_extract(tmp_path):
    path = tmp_path / "bad.tar"
    with tarfile.open(path, "w") as output:
        info = tarfile.TarInfo("workspace/../../escaped")
        info.size = 3
        output.addfile(info, io.BytesIO(b"bad"))
    with pytest.raises(migration.MigrationError, match="Unsafe archive path"):
        migration.read_archive(path, "workspace")
    assert not (tmp_path / "escaped").exists()
    with tarfile.open(path, "w") as output:
        for _ in range(2):
            info = tarfile.TarInfo("workspace")
            info.type = tarfile.DIRTYPE
            output.addfile(info)
    with pytest.raises(migration.MigrationError, match="Duplicate"):
        migration.read_archive(path, "workspace")


def test_backup_checksums_modes_and_full_identity_snapshot(backup):
    directory, manifest = backup
    assert migration.load_backup(directory) == manifest
    assert (directory / "passwd").read_text() == PASSWD
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in directory.iterdir())
    assert migration.read_archive(directory / "home.tar", "home")["user_123/.secret"]["sha256"] == hashlib.sha256(b"private token contents").hexdigest()
    (directory / "passwd").write_text(PASSWD + "tampered\n")
    with pytest.raises(migration.MigrationError, match="checksum"):
        migration.load_backup(directory)


def test_backup_rejects_public_modes_and_symlink_artifacts(backup):
    directory, _ = backup
    (directory / "group").chmod(0o644)
    with pytest.raises(migration.MigrationError, match="Unsafe backup"):
        migration.load_backup(directory)
    (directory / "group").unlink()
    (directory / "group").symlink_to(directory / "passwd")
    with pytest.raises(migration.MigrationError, match="Unsafe backup"):
        migration.load_backup(directory)


@ROOT_ONLY
def test_apply_verify_retry_preserve_sources_and_private_registry(backup, tmp_path):
    directory, manifest = backup
    root = original_workspace(tmp_path)
    before = {name: migration.digest_file(directory / name) for name in migration.ARTIFACTS}
    migration.install_target(root, directory, manifest)
    assert (root / "user_123/.secret").read_bytes() == b"private token contents"
    assert (root / "user_123/root-work.txt").read_bytes() == b"workspace work"
    migration.verify_target(root, manifest)
    first = migration.filesystem_inventory(root)
    migration.install_target(root, directory, manifest)
    assert migration.filesystem_inventory(root) == first
    assert {name: migration.digest_file(directory / name) for name in migration.ARTIFACTS} == before
    registry = root / ".sandbox/users.json"
    assert registry.stat().st_uid == 0 and stat.S_IMODE(registry.stat().st_mode) == 0o600
    assert stat.S_IMODE(registry.parent.stat().st_mode) == 0o700
    assert all((root / "user_123" / name).stat().st_uid == UID for name in migration.LAYOUT)
    assert not (tmp_path / "home").exists()


@ROOT_ONLY
def test_entire_conflict_preflight_precedes_any_mutation(backup, tmp_path):
    directory, manifest = backup
    root = original_workspace(tmp_path)
    (root / "user_123/root-work.txt").write_bytes(b"modified after export")
    before = migration.filesystem_inventory(root)
    with pytest.raises(migration.MigrationError, match="Changed/conflicting"):
        migration.install_target(root, directory, manifest)
    assert migration.filesystem_inventory(root) == before
    assert not (root / ".sandbox").exists()


@ROOT_ONLY
def test_partial_file_copy_can_retry_without_destructive_merge(backup, tmp_path, monkeypatch):
    directory, manifest = backup
    root = original_workspace(tmp_path)
    original_copy = migration.shutil.copyfileobj
    def interrupted(source, target):
        target.write(source.read(3))
        raise OSError("interrupted copy")
    monkeypatch.setattr(migration.shutil, "copyfileobj", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        migration.install_target(root, directory, manifest)
    assert not (root / "user_123/.secret").exists()
    assert (root / "user_123/root-work.txt").read_bytes() == b"workspace work"
    assert (root / ".sandbox/pending-data").read_bytes() == b"pri"
    monkeypatch.setattr(migration.shutil, "copyfileobj", original_copy)
    migration.install_target(root, directory, manifest)
    migration.verify_target(root, manifest)
    assert not (root / ".sandbox/pending-data").exists()


@ROOT_ONLY
def test_verify_detects_content_mode_and_registry_tampering(backup, tmp_path):
    directory, manifest = backup
    root = original_workspace(tmp_path)
    migration.install_target(root, directory, manifest)
    secret = root / "user_123/.secret"
    secret.chmod(0o644)
    with pytest.raises(migration.MigrationError, match="integrity"):
        migration.verify_target(root, manifest)
    secret.chmod(0o600)
    secret.write_bytes(b"wrong")
    with pytest.raises(migration.MigrationError, match="integrity"):
        migration.verify_target(root, manifest)
    secret.write_bytes(b"private token contents")
    (root / ".sandbox/users.json").write_text('{"version":1,"users":{}}')
    with pytest.raises(migration.MigrationError, match="Conflicting registry"):
        migration.verify_target(root, manifest)


@ROOT_ONLY
def test_layout_symlink_conflict_and_unsafe_registry_cannot_mutate(backup, tmp_path):
    directory, manifest = backup
    root = original_workspace(tmp_path)
    (root / "user_123/inbox").symlink_to(".")
    before = migration.filesystem_inventory(root)
    with pytest.raises(migration.MigrationError):
        migration.install_target(root, directory, manifest)
    assert migration.filesystem_inventory(root) == before
    (root / "user_123/inbox").unlink()
    (root / ".sandbox").symlink_to(tmp_path)
    with pytest.raises(migration.MigrationError, match="Unsafe registry"):
        migration.install_target(root, directory, manifest)


def test_stopped_container_is_required_without_executing_server(monkeypatch):
    raw = [{"Id": "old", "Image": "image", "State": {"Running": True},
            "Mounts": [{"Type": "volume", "Source": "/volume", "Destination": "/workspace"}]}]
    monkeypatch.setattr(migration, "docker", lambda *args: json.dumps(raw).encode())
    assert migration.container_info("old")["running"] is True
    with pytest.raises(migration.MigrationError, match="docker stop"):
        migration.container_info("old", stopped=True)


def test_other_running_volume_mounts_prevent_migration(monkeypatch):
    info = {"workspace_mount": {"Source": "/volume"}}
    containers = [{"Id": "other-live-server", "Mounts": [{"Source": "/volume", "Destination": "/data"}]}]
    def docker(*args):
        return b"other-live-server\n" if args[0] == "ps" else json.dumps(containers).encode()
    monkeypatch.setattr(migration, "docker", docker)
    with pytest.raises(migration.MigrationError, match="also mounts"):
        migration.require_offline_volume(info)
    containers[0]["Mounts"][0]["Source"] = "/unrelated-volume"
    migration.require_offline_volume(info)


def test_disappearing_unrelated_container_does_not_hide_live_volume_mount(monkeypatch):
    def docker(*args):
        if args[0] == "ps":
            return b"gone\nlive\n"
        if args[1] == "gone":
            raise migration.MigrationError("Docker command failed (inspect): error: no such object: gone")
        return json.dumps([{"Id": "live", "Mounts": [{"Source": "/volume"}]}]).encode()
    monkeypatch.setattr(migration, "docker", docker)
    with pytest.raises(migration.MigrationError, match="also mounts"):
        migration.require_offline_volume({"workspace_mount": {"Source": "/volume"}})




@ROOT_ONLY
def test_safe_symlink_is_relocated_without_dereferencing(backup, tmp_path):
    directory, manifest = backup
    workspace, homes = inventories()
    homes["user_123/secret-link"] = row("link", 0o777, target=".secret")
    archive(directory / "home.tar", "home", homes, {"user_123/.secret": b"private token contents"})
    manifest["checksums"]["home.tar"] = migration.digest_file(directory / "home.tar")
    manifest["plan"] = migration.capture_plan_from_backup(directory, {})
    root = original_workspace(tmp_path)
    migration.install_target(root, directory, manifest)
    link = root / "user_123/secret-link"
    assert link.is_symlink() and os.readlink(link) == ".secret"
    assert link.read_bytes() == b"private token contents"
    migration.verify_target(root, manifest)


def test_target_image_account_conflicts_fail_before_offline_apply():
    users = {"user_123": {"uid": UID, "gid": GID}}
    base_passwd = "root:x:0:0::/root:/bin/bash\n"
    migration.check_account_conflicts(users, base_passwd, GROUP)
    with pytest.raises(migration.MigrationError, match="UID conflict"):
        migration.check_account_conflicts(users, base_passwd + f"foreign:x:{UID}:999::/foreign:/bin/bash\n", GROUP)
    with pytest.raises(migration.MigrationError, match="GID conflict"):
        migration.check_account_conflicts(users, base_passwd, GROUP + f"foreign:x:{GID}:\n")
    with pytest.raises(migration.MigrationError, match="incompatible"):
        migration.check_account_conflicts(users, PASSWD, GROUP)
    with pytest.raises(migration.MigrationError, match="mcpusers"):
        migration.check_account_conflicts(users, base_passwd, "root:x:0:\n")
    with pytest.raises(migration.MigrationError, match="Unregistered"):
        migration.check_account_conflicts(users, base_passwd, GROUP + "user_orphan:x:9999:\n")
