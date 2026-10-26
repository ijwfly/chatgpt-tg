"""Persistent registry transitions, fail-closed provisioning and isolated children."""

import asyncio
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

SERVER = Path(__file__).resolve().parents[2] / "sandbox" / "server"
sys.path.insert(0, str(SERVER))
identities = importlib.import_module("identities")
common = importlib.import_module("common")
http_api = importlib.import_module("http_api")
spec = importlib.util.spec_from_file_location("sandbox_runtime_main", SERVER / "main.py")
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)
helper_spec = importlib.util.spec_from_file_location("sandbox_file_helper", SERVER / "file_helper.py")
helper = importlib.util.module_from_spec(helper_spec)
helper_spec.loader.exec_module(helper)


@pytest.fixture
def unix(tmp_path, monkeypatch):
    """Real filesystem/locking, emulated root ownership and NSS/account commands."""
    root = tmp_path / "workspace"
    root.mkdir(mode=0o755)
    accounts = {}
    groups = {"mcpusers": SimpleNamespace(gr_name="mcpusers", gr_gid=999, gr_mem=[])}
    owners = {}
    real_fstat, real_lstat = os.fstat, os.lstat

    def owned(st):
        values = list(st)
        values[4], values[5] = owners.get((st.st_dev, st.st_ino), (0, 0))
        return os.stat_result(values)

    monkeypatch.setattr(os, "fstat", lambda fd: owned(real_fstat(fd)))
    monkeypatch.setattr(os, "lstat", lambda path, **kw: owned(real_lstat(path, **kw)))

    def chown(fd, uid, gid):
        st = real_fstat(fd)
        owners[st.st_dev, st.st_ino] = (uid, gid)

    monkeypatch.setattr(os, "fchown", chown)
    check_root = identities.check_root_directory
    monkeypatch.setattr(identities, "check_root_directory",
                        lambda path, mode=None: check_root(path, mode) if str(path).startswith(str(root)) else None)

    def lookup(collection, field, value):
        for entry in collection.values():
            if getattr(entry, field) == value:
                return entry
        raise KeyError(value)

    monkeypatch.setattr(identities.pwd, "getpwnam", lambda name: lookup(accounts, "pw_name", name))
    monkeypatch.setattr(identities.pwd, "getpwuid", lambda uid: lookup(accounts, "pw_uid", uid))
    monkeypatch.setattr(identities.pwd, "getpwall", lambda: list(accounts.values()))
    monkeypatch.setattr(identities.grp, "getgrnam", lambda name: lookup(groups, "gr_name", name))
    monkeypatch.setattr(identities.grp, "getgrgid", lambda gid: lookup(groups, "gr_gid", gid))
    monkeypatch.setattr(identities.grp, "getgrall", lambda: list(groups.values()))
    calls = []

    def run(*args):
        calls.append(args)
        name = args[-1]
        if args[0] == "groupadd":
            groups[name] = SimpleNamespace(gr_name=name, gr_gid=int(args[2]), gr_mem=[])
        elif args[0] == "useradd":
            accounts[name] = SimpleNamespace(pw_name=name, pw_uid=int(args[args.index("-u") + 1]),
                pw_gid=int(args[args.index("-g") + 1]), pw_dir=args[args.index("-d") + 1], pw_shell="/bin/bash")
            groups["mcpusers"].gr_mem.append(name)
        elif args[0] == "usermod":
            groups["mcpusers"].gr_mem.append(name)
        else:
            raise AssertionError(args)

    monkeypatch.setattr(identities, "_run", run)
    registry = identities.IdentityRegistry(str(root))
    return SimpleNamespace(root=root, registry=registry, accounts=accounts, groups=groups,
                           calls=calls, owners=owners, chown=chown)


def test_empty_volume_and_reversed_requests_keep_ids_and_configs(unix):
    unix.registry.restore()
    unix.registry.ensure_user("user_123")
    unix.registry.ensure_user("user_456")
    before = json.loads(Path(unix.registry.path).read_text())
    private = unix.root / "user_123" / ".config" / "cli"
    private.parent.mkdir()
    private.write_text("private login")
    unix.accounts.clear()
    for name in ("user_123", "user_456"):
        del unix.groups[name]
    unix.groups["mcpusers"].gr_mem.clear()
    unix.registry.restore()
    unix.registry.ensure_user("user_456")
    unix.registry.ensure_user("user_123")
    assert json.loads(Path(unix.registry.path).read_text()) == before
    assert private.read_text() == "private login"
    for name, entry in before["users"].items():
        account = unix.accounts[name]
        assert (account.pw_uid, account.pw_gid, account.pw_dir) == (entry["uid"], entry["gid"], str(unix.root / name))
        assert name in unix.groups["mcpusers"].gr_mem
        for directory in (unix.root / name, *(unix.root / name / d for d in identities.LAYOUT_DIRS)):
            assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(Path(unix.registry.path).stat().st_mode) == 0o600
    assert stat.S_IMODE(Path(unix.registry.metadata).stat().st_mode) == 0o700


@pytest.mark.parametrize("kind", ["home", "account", "group"])
def test_old_volume_or_unregistered_identity_requires_migration(unix, kind):
    if kind == "home":
        (unix.root / "user_123").mkdir()
    elif kind == "account":
        unix.accounts["user_123"] = SimpleNamespace(pw_name="user_123")
    else:
        unix.groups["user_123"] = SimpleNamespace(gr_name="user_123")
    with pytest.raises(identities.IdentityError, match="migrate_sandbox_homes.py"):
        unix.registry.restore()
    assert not Path(unix.registry.path).exists()
    assert unix.calls == []


@pytest.mark.parametrize("data", [
    {"version": True, "users": {}},
    {"version": 2, "users": {}},
    {"version": 1, "users": {"root": {"uid": 1, "gid": 1}}},
    {"version": 1, "users": {"user_a": {"uid": 0, "gid": 1}}},
    {"version": 1, "users": {"user_a": {"uid": True, "gid": 1}}},
    {"version": 1, "users": {"user_a": {"uid": 1, "gid": 1}, "user_b": {"uid": 1, "gid": 2}}},
])
def test_malformed_registry_fails_closed(unix, data):
    unix.registry.restore()
    Path(unix.registry.path).write_text(json.dumps(data))
    with pytest.raises(identities.IdentityError):
        unix.registry.restore()
    assert unix.calls == []


def test_duplicate_registry_keys_rejected(unix):
    unix.registry.restore()
    Path(unix.registry.path).write_text('{"version":1,"users":{},"users":{}}')
    with pytest.raises(identities.IdentityError, match="Duplicate"):
        unix.registry.restore()


@pytest.mark.parametrize("target", ["metadata", "registry", "lock", "home", "layout"])
def test_symlinks_rejected_without_touching_target(unix, target):
    unix.registry.restore()
    unix.registry.ensure_user("user_a")
    outside = unix.root / "outside"
    outside.mkdir(mode=0o700)
    marker = outside / "marker"
    marker.write_text("untouched")
    if target == "metadata":
        path = Path(unix.registry.metadata)
        path.rename(unix.root / "saved_metadata")
    elif target in ("registry", "lock"):
        path = Path(unix.registry.path) if target == "registry" else Path(unix.registry.metadata) / "users.lock"
        path.unlink()
    elif target == "home":
        path = unix.root / "user_a"
        path.rename(unix.root / "saved_home")
    else:
        path = unix.root / "user_a" / "tmp"
        path.rmdir()
    path.symlink_to(outside, target_is_directory=True)
    with pytest.raises(identities.IdentityError):
        unix.registry.ensure_user("user_a")
    assert marker.read_text() == "untouched"
    assert stat.S_IMODE(outside.stat().st_mode) == 0o700


@pytest.mark.parametrize("target,mode", [("root", 0o777), ("metadata", 0o755), ("registry", 0o644), ("lock", 0o666)])
def test_unsafe_permissions_rejected(unix, target, mode):
    unix.registry.restore()
    path = {"root": unix.root, "metadata": Path(unix.registry.metadata),
            "registry": Path(unix.registry.path), "lock": Path(unix.registry.metadata) / "users.lock"}[target]
    path.chmod(mode)
    with pytest.raises(identities.IdentityError):
        unix.registry.restore()


def test_registry_id_conflict_does_not_remap(unix):
    unix.registry.restore()
    unix.registry.ensure_user("user_a")
    original = Path(unix.registry.path).read_bytes()
    unix.accounts.clear()
    unix.accounts["system"] = SimpleNamespace(pw_name="system", pw_uid=1001)
    with pytest.raises(identities.IdentityError, match="UID"):
        unix.registry.restore()
    assert Path(unix.registry.path).read_bytes() == original


def test_interrupted_allocation_restores_from_persisted_intent(unix, monkeypatch):
    unix.registry.restore()
    run = identities._run
    monkeypatch.setattr(identities, "_run", lambda *args: (_ for _ in ()).throw(subprocess.CalledProcessError(1, args)))
    with pytest.raises(identities.IdentityError):
        unix.registry.ensure_user("user_a")
    entry = json.loads(Path(unix.registry.path).read_text())["users"]["user_a"]
    monkeypatch.setattr(identities, "_run", run)
    unix.registry.restore()
    assert unix.accounts["user_a"].pw_uid == entry["uid"]


def test_layout_ownership_conflict_never_recursive_chowns(unix):
    unix.registry.restore()
    unix.registry.ensure_user("user_a")
    tmp = unix.root / "user_a" / "tmp"
    st = tmp.stat()
    unix.owners[st.st_dev, st.st_ino] = (2000, 2000)
    with pytest.raises(identities.IdentityError, match="ownership"):
        unix.registry.ensure_user("user_a")
    assert unix.owners[st.st_dev, st.st_ino] == (2000, 2000)


def test_children_have_real_home_cwd_private_umask_and_no_startup_or_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "WORKSPACE_ROOT", str(tmp_path))
    home = tmp_path / "user_a"
    home.mkdir()
    startup = home / "poison"
    startup.write_text("echo POISON; export LEAK=poison\n")
    (home / ".bashrc").write_text(startup.read_text())
    (home / ".profile").write_text(startup.read_text())
    for key in ("SANDBOX_API_TOKEN", "OPENAI_API_KEY", "PYTHONPATH", "PYTHONHOME", "BASH_ENV", "ENV"):
        monkeypatch.setenv(key, str(startup))
    command = common.bash_cmd("user_a", 'printf "%s\\n" "$HOME" "$USER" "$LOGNAME"; pwd; umask; env; touch private')
    result = subprocess.run(command[5:], capture_output=True, text=True, check=True,
                            **common.user_process_options("user_a"))
    lines = result.stdout.splitlines()
    assert lines[:5] == [str(home), "user_a", "user_a", str(home), "0077"]
    assert "POISON" not in result.stdout
    assert not any(key + "=" in result.stdout for key in ("SANDBOX_API_TOKEN", "OPENAI_API_KEY", "PYTHONPATH", "PYTHONHOME", "BASH_ENV", "LEAK"))
    assert stat.S_IMODE((home / "private").stat().st_mode) == 0o600


def test_python_helper_ignores_poisoned_user_packages(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "WORKSPACE_ROOT", str(tmp_path))
    home = tmp_path / "user_a"
    home.mkdir()
    (home / "json.py").write_text('raise RuntimeError("POISON")')
    monkeypatch.setenv("PYTHONPATH", str(home))
    command = common.helper_cmd("user_a")
    # Use the test interpreter for portability; preserve isolated flags and exact env.
    command[command.index("/usr/bin/python3")] = sys.executable
    result = subprocess.run(command[5:], input=json.dumps({"op": "stat", "path": str(home)}),
                            capture_output=True, text=True, check=True,
                            **common.user_process_options("user_a"))
    assert json.loads(result.stdout) == {"type": "dir"}


async def middleware_request(app, path="/exec", headers=()):
    events = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(event):
        events.append(event)
    await app({"type": "http", "path": path, "headers": list(headers)}, receive, send)
    return events


def test_authentication_precedes_any_provisioning_and_health_is_public(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "ensure_user", lambda name: calls.append(name))
    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200})
    app = main.UserIdMiddleware(downstream, api_token="private")
    for authorization in ([], [(b"authorization", b"Bearer wrong")],
                          [(b"authorization", b"Bearer private"), (b"authorization", b"Bearer private")]):
        events = asyncio.run(middleware_request(app, headers=[(b"x-user-id", b"123"), *authorization]))
        assert events[0]["status"] == 401
    assert calls == []
    events = asyncio.run(middleware_request(app, path="/health"))
    assert events[0]["status"] == 200
    assert calls == []
    events = asyncio.run(middleware_request(app, headers=[(b"x-user-id", b"123"), (b"authorization", b"Bearer private")]))
    assert events[0]["status"] == 200
    assert calls == ["user_123"]
    assert common.get_current_user() is None


def test_provisioning_errors_are_structured_and_do_not_leak(monkeypatch):
    def fail(name):
        raise identities.IdentityError("secret registry contents")
    monkeypatch.setattr(main, "ensure_user", fail)
    app = main.UserIdMiddleware(None, api_token="private")
    events = asyncio.run(middleware_request(app, headers=[(b"x-user-id", b"123"), (b"authorization", b"Bearer private")]))
    assert events[0]["status"] == 503
    assert json.loads(events[1]["body"]) == {"error": "Sandbox user provisioning failed"}


def test_missing_token_prevents_startup_before_volume_mutation(monkeypatch):
    monkeypatch.delenv("SANDBOX_API_TOKEN", raising=False)
    monkeypatch.setattr(main, "initialize_sandbox", lambda: pytest.fail("must validate token first"))
    with pytest.raises(RuntimeError, match="SANDBOX_API_TOKEN"):
        main.create_app()


def test_stream_upload_exclusive_and_failed_partial_cleanup(tmp_path, monkeypatch):
    destination = tmp_path / "upload.bin"
    destination.write_bytes(b"existing")
    monkeypatch.setattr(helper.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"new")))
    monkeypatch.setattr(helper.sys, "stdout", io.StringIO())
    monkeypatch.setattr(helper.sys, "stderr", io.StringIO())
    assert helper.stream_write(str(destination)) == 3
    assert destination.read_bytes() == b"existing"
    fresh = tmp_path / "fresh.bin"
    assert helper.stream_write(str(fresh)) == 0
    assert fresh.read_bytes() == b"new"
    def interrupted(source, sink):
        sink.write(b"partial")
        raise OSError("interrupted")
    monkeypatch.setattr(helper.shutil, "copyfileobj", interrupted)
    partial = tmp_path / "partial.bin"
    assert helper.stream_write(str(partial)) == 1
    assert not partial.exists()
    assert destination.read_bytes() == b"existing"


def test_concurrent_requests_allocate_once_without_id_collisions(unix):
    from concurrent.futures import ThreadPoolExecutor

    unix.registry.restore()
    names = ["user_a", "user_b", "user_c"] * 4
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(unix.registry.ensure_user, names))
    users = json.loads(Path(unix.registry.path).read_text())["users"]
    assert set(users) == set(names)
    assert len({entry["uid"] for entry in users.values()}) == 3
    assert len({entry["gid"] for entry in users.values()}) == 3
    assert len([call for call in unix.calls if call[0] == "useradd"]) == 3




@pytest.mark.parametrize("kind", ["hardlink", "directory", "fifo"])
def test_registry_must_be_single_link_regular_file(unix, kind):
    unix.registry.restore()
    path = Path(unix.registry.path)
    if kind == "hardlink":
        os.link(path, unix.root / "linked_registry")
    else:
        path.unlink()
        if kind == "directory":
            path.mkdir(mode=0o600)
        else:
            os.mkfifo(path, 0o600)
    with pytest.raises(identities.IdentityError):
        unix.registry.restore()


def test_shared_skills_refuses_symlinks_and_writable_files(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    public = root / "public_skills"
    public.mkdir(parents=True)
    monkeypatch.setattr(common, "WORKSPACE_ROOT", str(root))
    monkeypatch.setattr(common, "PUBLIC_SKILLS_DIR", str(public))
    monkeypatch.setattr(common, "check_root_directory", lambda path: None)
    real_lstat = os.lstat
    def root_owned(path):
        values = list(real_lstat(path))
        values[4], values[5] = 0, 0
        return os.stat_result(values)
    monkeypatch.setattr(os, "lstat", root_owned)
    secret = tmp_path / "secret"
    secret.write_text("private")
    linked = public / "linked"
    linked.symlink_to(secret)
    with pytest.raises(identities.IdentityError):
        common.sync_public_skills(source=str(tmp_path / "missing"))
    assert secret.read_text() == "private"
    linked.unlink()
    writable = public / "writable"
    writable.write_text("unsafe")
    writable.chmod(0o666)
    with pytest.raises(identities.IdentityError):
        common.sync_public_skills(source=str(tmp_path / "missing"))


def test_fileop_upload_download_share_isolated_home_and_upload_conflicts(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "WORKSPACE_ROOT", str(tmp_path))
    home = tmp_path / "user_a"
    home.mkdir()
    monkeypatch.setenv("SANDBOX_API_TOKEN", "must-not-reach-child")
    original_spawn = asyncio.create_subprocess_exec

    async def spawn(*command, **options):
        command = list(command)
        command[command.index("/usr/bin/python3")] = sys.executable
        return await original_spawn(*command[5:], **options)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

    class Upload:
        path_params = {"path": "inbox/document.bin"}

        async def stream(self):
            yield b"document bytes"

    async def exercise():
        context = common.current_user.set("user_a")
        try:
            result = await common.run_file_op("user_a", {"op": "write", "path": str(home / "config"), "content": "private"})
            assert result["status"] == "ok"
            uploaded = await http_api.files_put(Upload())
            assert uploaded.status_code == 200
            assert json.loads(uploaded.body)["path"] == "inbox/document.bin"
            duplicate = await http_api.files_put(Upload())
            assert duplicate.status_code == 409
            download = await http_api._stream_download("user_a", str(home / "inbox" / "document.bin"))
            contents = b"".join([chunk async for chunk in download.body_iterator])
            assert contents == b"document bytes"
        finally:
            common.current_user.reset(context)

    asyncio.run(exercise())
    assert stat.S_IMODE((home / "config").stat().st_mode) == 0o600
    assert stat.S_IMODE((home / "inbox" / "document.bin").stat().st_mode) == 0o600
