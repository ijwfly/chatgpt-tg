"""Root-controlled, durable identities for homes on the workspace volume."""

import fcntl
import grp
import json
import os
import pwd
import re
import stat
import subprocess
import tempfile
from contextlib import contextmanager

LINUX_USER_RE = re.compile(r"^user_[a-z0-9_]{1,27}$")
LAYOUT_DIRS = ("inbox", "projects", "outbox", "tmp", "skills")
SYSTEM_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}
MIGRATION_MESSAGE = (
    "Unregistered sandbox home or account found. Stop the sandbox, back up the volume, "
    "and run scripts/migrate_sandbox_homes.py before restarting."
)


class IdentityError(RuntimeError):
    """An unsafe or inconsistent persistent identity; never adopt it implicitly."""


def check_root_directory(path, mode=None):
    st = os.lstat(path)
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_gid != 0
            or stat.S_IMODE(st.st_mode) & 0o022
            or (mode is not None and stat.S_IMODE(st.st_mode) != mode)):
        raise IdentityError(f"Unsafe root-controlled directory: {path}")


def _check_metadata(fd):
    st = os.fstat(fd)
    if (not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_gid != 0
            or stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1):
        raise IdentityError("Unsafe sandbox registry metadata permissions or type")


def _lookup(lookup, key):
    try:
        return lookup(key)
    except KeyError:
        return None


def _run(*args):
    subprocess.run(("/usr/sbin/" + args[0], *args[1:]),
                   check=True, capture_output=True, env=SYSTEM_ENV, cwd="/")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise IdentityError("Duplicate sandbox registry key")
        result[key] = value
    return result


class IdentityRegistry:
    def __init__(self, workspace_root="/workspace"):
        self.root = workspace_root
        self.metadata = os.path.join(workspace_root, ".sandbox")
        self.path = os.path.join(self.metadata, "users.json")

    @contextmanager
    def _locked(self):
        try:
            # Every parent must be a real, root-controlled directory too.
            parent = os.path.abspath(self.root)
            while True:
                check_root_directory(parent)
                if parent == "/":
                    break
                parent = os.path.dirname(parent)
            try:
                os.mkdir(self.metadata, 0o700)
                root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(root_fd)
                finally:
                    os.close(root_fd)
            except FileExistsError:
                pass
            check_root_directory(self.metadata, 0o700)
            fd = os.open(os.path.join(self.metadata, "users.lock"),
                         os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            try:
                _check_metadata(fd)
                fcntl.flock(fd, fcntl.LOCK_EX)
                shared_group = _lookup(grp.getgrnam, "mcpusers")
                if shared_group is None or shared_group.gr_gid <= 0:
                    raise IdentityError("Required nonroot mcpusers group is missing or unsafe")
                yield
            finally:
                os.close(fd)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise IdentityError("Sandbox identity provisioning failed; inspect volume and account state") from exc

    def _load(self):
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            _check_metadata(stream.fileno())
            try:
                data = json.load(stream, object_pairs_hook=_unique_object)
            except (ValueError, UnicodeError) as exc:
                raise IdentityError("Malformed sandbox identity registry") from exc
        if (not isinstance(data, dict) or set(data) != {"version", "users"}
                or type(data["version"]) is not int or data["version"] != 1
                or not isinstance(data["users"], dict)):
            raise IdentityError("Unsupported sandbox identity registry")
        uids, gids = set(), set()
        for name, entry in data["users"].items():
            if (not LINUX_USER_RE.fullmatch(name) or not isinstance(entry, dict)
                    or set(entry) != {"uid", "gid"}
                    or any(type(entry[k]) is not int or not 0 < entry[k] < 2**32 - 1
                           for k in ("uid", "gid"))):
                raise IdentityError("Invalid sandbox identity registry entry")
            if entry["uid"] in uids or entry["gid"] in gids:
                raise IdentityError("Conflicting sandbox identity registry IDs")
            uids.add(entry["uid"])
            gids.add(entry["gid"])
        return data

    def _save(self, data):
        fd, temporary = tempfile.mkstemp(prefix=".users-", dir=self.metadata)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                os.fchown(stream.fileno(), 0, 0)
                json.dump(data, stream, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.metadata, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _check_unregistered(self, data):
        users = data["users"] if data else {}
        for name in os.listdir(self.root):
            if name.startswith("user_") and name not in users:
                raise IdentityError(MIGRATION_MESSAGE)
        for account in pwd.getpwall():
            if LINUX_USER_RE.fullmatch(account.pw_name) and account.pw_name not in users:
                raise IdentityError(MIGRATION_MESSAGE)
        for group in grp.getgrall():
            if LINUX_USER_RE.fullmatch(group.gr_name) and group.gr_name not in users:
                raise IdentityError(MIGRATION_MESSAGE)

    def _check_identity(self, name, entry):
        uid, gid = entry["uid"], entry["gid"]
        account = _lookup(pwd.getpwnam, name)
        uid_account = _lookup(pwd.getpwuid, uid)
        group = _lookup(grp.getgrnam, name)
        gid_group = _lookup(grp.getgrgid, gid)
        if (account is not None and (account.pw_uid != uid or account.pw_gid != gid
                or account.pw_dir != os.path.join(self.root, name) or account.pw_shell != "/bin/bash")):
            raise IdentityError(f"Sandbox account conflicts with registry: {name}")
        if uid_account is not None and uid_account.pw_name != name:
            raise IdentityError(f"Sandbox UID conflicts with system account: {name}")
        if group is not None and group.gr_gid != gid:
            raise IdentityError(f"Sandbox group conflicts with registry: {name}")
        if gid_group is not None and gid_group.gr_name != name:
            raise IdentityError(f"Sandbox GID conflicts with system group: {name}")
        # Detect duplicate/alias NSS entries hidden by single-entry lookups.
        matching_accounts = [a for a in pwd.getpwall() if a.pw_uid == uid or a.pw_name == name]
        matching_groups = [g for g in grp.getgrall() if g.gr_gid == gid or g.gr_name == name]
        if len(matching_accounts) > 1 or any(a.pw_name != name or a.pw_uid != uid for a in matching_accounts):
            raise IdentityError(f"Sandbox UID has another owner or alias: {name}")
        if len(matching_groups) > 1 or any(g.gr_name != name or g.gr_gid != gid for g in matching_groups):
            raise IdentityError(f"Sandbox GID has another owner or alias: {name}")
        return account, group

    def _home(self, name, entry):
        home = os.path.join(self.root, name)
        created = False
        try:
            os.mkdir(home, 0o700)
            created = True
        except FileExistsError:
            pass
        fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if created:
                os.fchown(fd, entry["uid"], entry["gid"])
                os.fchmod(fd, 0o700)
            self._check_home(fd, entry)
            for directory in LAYOUT_DIRS:
                made = False
                try:
                    os.mkdir(directory, 0o700, dir_fd=fd)
                    made = True
                except FileExistsError:
                    pass
                child = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    if made:
                        os.fchown(child, entry["uid"], entry["gid"])
                        os.fchmod(child, 0o700)
                    self._check_home(child, entry)
                finally:
                    os.close(child)
        finally:
            os.close(fd)

    @staticmethod
    def _check_home(fd, entry):
        st = os.fstat(fd)
        if (st.st_uid != entry["uid"] or st.st_gid != entry["gid"]
                or stat.S_IMODE(st.st_mode) != 0o700):
            raise IdentityError("Sandbox home/layout ownership or permissions conflict; migration required")

    def _restore(self, name, entry):
        account, group = self._check_identity(name, entry)
        # Validate an existing home before modifying OS accounts.
        home = os.path.join(self.root, name)
        if os.path.lexists(home):
            fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                self._check_home(fd, entry)
            finally:
                os.close(fd)
        if group is None:
            _run("groupadd", "-g", str(entry["gid"]), name)
        if account is None:
            _run("useradd", "-M", "-d", home, "-s", "/bin/bash", "-u", str(entry["uid"]),
                 "-g", str(entry["gid"]), "-G", "mcpusers", name)
        elif name not in grp.getgrnam("mcpusers").gr_mem:
            _run("usermod", "-a", "-G", "mcpusers", name)
        self._home(name, entry)

    def restore(self):
        """Restore every recorded account before the server accepts requests."""
        with self._locked():
            data = self._load()
            self._check_unregistered(data)
            if data is None:
                data = {"version": 1, "users": {}}
                self._save(data)
            # Preflight all identities before creating any of them.
            for name, entry in data["users"].items():
                self._check_identity(name, entry)
            for name, entry in data["users"].items():
                self._restore(name, entry)

    def ensure_user(self, name):
        if not LINUX_USER_RE.fullmatch(name):
            raise IdentityError("Invalid sandbox account name")
        with self._locked():
            data = self._load()
            if data is None:
                raise IdentityError("Sandbox identity registry is not initialized")
            self._check_unregistered(data)
            if name not in data["users"]:
                used_uids = {a.pw_uid for a in pwd.getpwall()} | {u["uid"] for u in data["users"].values()}
                used_gids = {g.gr_gid for g in grp.getgrall()} | {u["gid"] for u in data["users"].values()}
                number = 1001
                while number in used_uids or number in used_gids:
                    number += 1
                if number >= 2**32 - 1:
                    raise IdentityError("No free sandbox identity IDs")
                data["users"][name] = {"uid": number, "gid": number}
                # Persist first: a crash between registry/account/home steps is recoverable.
                self._save(data)
            self._restore(name, data["users"][name])
