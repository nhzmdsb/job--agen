"""Private GitHub JSON replicas with optimistic writes and durable conflict records."""
import base64
import hashlib
import json
import os
import subprocess
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .store import Store, private_home, Conflict


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def record_path(collection, record_id):
    return f"records/{collection}/{hashlib.sha256(record_id.encode()).hexdigest()}.json"


class GitHub:
    def __init__(self, repository):
        # Git Credential Manager keeps the secret; it is neither stored nor logged here.
        result = subprocess.run(["git", "-c", "credential.interactive=never", "credential", "fill"], input="protocol=https\nhost=github.com\n\n", text=True, capture_output=True, timeout=20)
        fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if result.returncode or not fields.get("password"):
            raise RuntimeError("GitHub 凭据不可用，请先在此电脑授权 Git")
        self.token = fields["password"]
        self.repository = repository
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        metadata = self.request("")
        if not metadata.get("private"):
            raise RuntimeError("拒绝向公开仓库同步个人资料")
        self.branch = metadata["default_branch"]

    def request(self, path, data=None, method=None):
        req = urllib.request.Request("https://api.github.com/repos/" + self.repository + path, data=json.dumps(data).encode() if data is not None else None, method=method, headers={"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json", "Content-Type": "application/json", "User-Agent": "JobAgent-V0"})
        try:
            with self.opener.open(req, timeout=25) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"GitHub 请求失败（HTTP {exc.code}），本地资料保留，下次重试") from None
        except (OSError, ValueError):
            raise RuntimeError("无法连接 GitHub，本地资料保留，下次重试") from None

    def inventory(self):
        tree = self.request(f"/git/trees/{self.branch}?recursive=1")
        if tree.get("truncated"):
            raise RuntimeError("远端目录超出读取范围，停止同步")
        return {r["path"]: r["sha"] for r in tree["tree"] if r["type"] == "blob"}

    def read(self, sha):
        blob = self.request("/git/blobs/" + sha)
        return base64.b64decode(blob["content"])

    def write(self, path, content, sha=None):
        payload = {"message": "Update private workspace " + path, "content": base64.b64encode(content).decode(), "branch": self.branch}
        if sha:
            payload["sha"] = sha
        return self.request("/contents/" + path, payload, "PUT")["content"]["sha"]


@contextmanager
def process_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as file:
        file.seek(0)
        file.write(b"0")
        file.flush()
        file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("另一同步正在运行，请稍后检查") from None
        try:
            yield
        finally:
            file.seek(0)
            if os.name == "nt":
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(file, fcntl.LOCK_UN)


class Sync:
    def __init__(self, store, home=None, remote_factory=GitHub):
        self.store = store
        self.home = Path(home or private_home())
        self.remote_factory = remote_factory
        self.event = threading.Event()
        self.running = False
        self.last = {"state": "local", "message": "本地保存，云端尚未连接"}

    def config(self):
        file = self.home / "settings.json"
        return json.loads(file.read_text(encoding="utf-8-sig")) if file.exists() else {}

    def status(self):
        with self.store.connection() as db:
            pending = db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
            conflicts = [{"collection": r["collection"], "id": r["id"], "local": json.loads(r["local"]), "remote": json.loads(r["remote"])} for r in db.execute("SELECT * FROM conflicts")]
        return {**self.last, "running": self.running, "pending": pending, "conflicts": conflicts, "repository": self.config().get("repository")}

    def wake(self):
        self.event.set()

    def conflict(self, local, remote):
        r = local["record"]
        with self.store.connection() as db:
            db.execute("INSERT OR REPLACE INTO conflicts VALUES (?,?,?,?)", (r["collection"], r["id"], json.dumps(local, ensure_ascii=False), json.dumps(remote, ensure_ascii=False)))

    def mark(self, pack, sha):
        r = pack["record"]
        with self.store.connection() as db:
            db.execute("INSERT OR REPLACE INTO sync_base VALUES (?,?,?,?)", (r["collection"], r["id"], digest(pack), sha))
            db.execute("DELETE FROM outbox WHERE collection=? AND id=? AND version=?", (r["collection"], r["id"], r["version"]))

    def adopt(self, pack, sha, expected_version):
        self.store.validate_pack(pack)
        r = pack["record"]
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT version FROM records WHERE collection=? AND id=?", (r["collection"], r["id"])).fetchone()
            if (current[0] if current else 0) != expected_version:
                raise Conflict("local record changed during sync")
            if r["collection"] == "jobs" and r["data"].get("company"):
                from .jobs import identity
                key = identity(r["data"])
                for other in db.execute("SELECT id,data FROM records WHERE collection='jobs' AND id!=?", (r["id"],)):
                    data = json.loads(other["data"])
                    if data.get("company") and (identity(data) == key or key in data.get("dedupe_keys", [])):
                        raise Conflict("远端出现重复岗位（包含垃圾桶），已停止导入，请检查私有仓库")
            if r["collection"] == "profiles" and r["id"] == "company-quotas":
                from .jobs import validate_quotas
                validate_quotas(r["data"])
            self.store.insert_pack(db, pack)
            db.execute("DELETE FROM outbox WHERE collection=? AND id=?", (r["collection"], r["id"]))
            db.execute("INSERT OR REPLACE INTO sync_base VALUES (?,?,?,?)", (r["collection"], r["id"], digest(pack), sha))

    def assets(self, remote, inventory, pack, upload):
        if pack["record"]["collection"] != "artifacts":
            return
        data = pack["record"]["data"]
        sha256 = data.get("sha256")
        if not sha256:
            return  # Legacy name/summary is not an original file.
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise ValueError("invalid attachment hash")
        path = "attachments/" + sha256
        file = self.home / path
        remote_sha = inventory.get(path)
        if upload:
            content = file.read_bytes()
            if hashlib.sha256(content).hexdigest() != sha256:
                raise ValueError("本地附件校验失败")
            if not remote_sha:
                inventory[path] = remote.write(path, content)
            elif hashlib.sha256(remote.read(remote_sha)).hexdigest() != sha256:
                raise ValueError("远端附件校验失败")
        elif not file.exists():
            if not remote_sha:
                raise ValueError("远端附件原文件缺失")
            content = remote.read(remote_sha)
            if hashlib.sha256(content).hexdigest() != sha256:
                raise ValueError("远端附件校验失败")
            file.parent.mkdir(parents=True, exist_ok=True)
            temporary = file.with_suffix(".tmp")
            temporary.write_bytes(content)
            temporary.replace(file)
        elif hashlib.sha256(file.read_bytes()).hexdigest() != sha256:
            raise ValueError("本地附件校验失败")

    def run(self):
        repository = self.config().get("repository")
        if not repository:
            return self.status()
        self.running = True
        try:
            with process_lock(self.home / "sync.lock"):
                remote = self.remote_factory(repository)
                inventory = remote.inventory()
                local_records = self.store.export()["records"]
                local_paths = {record_path(r["collection"], r["id"]): (r["collection"], r["id"]) for r in local_records}
                remote_paths = {p for p in inventory if p.startswith("records/") and p.endswith(".json")}
                for path in sorted(set(local_paths) | remote_paths):
                    remote_pack = json.loads(remote.read(inventory[path])) if path in remote_paths else None
                    if remote_pack:
                        self.store.validate_pack(remote_pack)
                        r = remote_pack["record"]
                        if record_path(r["collection"], r["id"]) != path:
                            raise ValueError("record filename does not match its identity")
                    key = local_paths.get(path) or (r["collection"], r["id"])
                    with self.store.connection() as db:
                        base = db.execute("SELECT * FROM sync_base WHERE collection=? AND id=?", key).fetchone()
                        blocked = db.execute("SELECT 1 FROM conflicts WHERE collection=? AND id=?", key).fetchone()
                    if blocked:
                        continue
                    local = self.store.pack(*key) if path in local_paths else None
                    local_hash = digest(local) if local else None
                    remote_hash = digest(remote_pack) if remote_pack else None
                    if local_hash == remote_hash:
                        self.assets(remote, inventory, local, False)
                        self.mark(local, inventory[path])
                    elif local is None:
                        self.assets(remote, inventory, remote_pack, False)
                        self.adopt(remote_pack, inventory[path], 0)
                    elif base and local_hash == base["hash"] and remote_pack:
                        # A valid external edit must keep the already accepted history prefix.
                        if remote_pack["history"][:len(local["history"])] != local["history"]:
                            self.conflict(local, remote_pack)
                            continue
                        self.assets(remote, inventory, remote_pack, False)
                        self.adopt(remote_pack, inventory[path], local["record"]["version"])
                    elif (not remote_pack and not base) or (base and remote_hash == base["hash"]):
                        self.assets(remote, inventory, local, True)
                        sha = remote.write(path, json.dumps(local, ensure_ascii=False, indent=2).encode(), inventory.get(path))
                        self.mark(local, sha)
                    else:
                        self.conflict(local, remote_pack)
                self.last = {"state": "connected", "message": "私有 GitHub 已连接", "checked_at": datetime.now(timezone.utc).isoformat()}
        except (RuntimeError, ValueError, OSError, KeyError, TypeError, Conflict) as exc:
            self.last = {"state": "error", "message": str(exc), "checked_at": datetime.now(timezone.utc).isoformat()}
        finally:
            self.running = False
        return self.status()

    def worker(self):
        next_check = retry_after = 0
        while True:
            import time
            with self.store.connection() as db:
                pending = db.execute("SELECT 1 FROM outbox o WHERE NOT EXISTS (SELECT 1 FROM conflicts c WHERE c.collection=o.collection AND c.id=o.id) LIMIT 1").fetchone() if self.config().get("repository") else None
            if self.event.is_set() or time.monotonic() >= next_check or (pending and time.monotonic() >= retry_after):
                self.event.clear()
                self.run()
                next_check = time.monotonic() + 21600
                retry_after = time.monotonic() + (30 if self.last["state"] == "error" else 5)
            self.event.wait(3)

    def start(self):
        threading.Thread(target=self.worker, daemon=True, name="private-sync").start()
