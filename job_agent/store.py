"""Transactional JSON storage. Business interpretation belongs to the agent."""
import json
import os
import sqlite3
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

COLLECTIONS = ("sources", "leads", "jobs", "runs", "artifacts", "profiles")


def private_home():
    return Path(os.environ.get("JOB_AGENT_HOME") or (Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share")) / "JobAgent")).expanduser().resolve()


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get("JOB_AGENT_DB") or private_home() / "workspace.sqlite3").expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        legacy = Path(__file__).resolve().parent.parent / "data/workspace.sqlite3"
        migrated = False
        if path is None and not os.environ.get("JOB_AGENT_DB") and not self.path.exists() and legacy.exists() and legacy != self.path:
            backup = self.path.parent / "backups" / ("legacy-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".sqlite3")
            backup.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(legacy.as_uri() + "?mode=ro", uri=True) as source:
                with sqlite3.connect(backup) as target:
                    source.backup(target)
                with sqlite3.connect(self.path) as target:
                    source.backup(target)
            migrated = True
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS records (
                  collection TEXT NOT NULL, id TEXT NOT NULL, version INTEGER NOT NULL,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, data TEXT NOT NULL,
                  PRIMARY KEY(collection, id));
                CREATE TABLE IF NOT EXISTS history (
                  collection TEXT NOT NULL, id TEXT NOT NULL, version INTEGER NOT NULL,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, data TEXT NOT NULL,
                  actor TEXT NOT NULL, reason TEXT NOT NULL,
                  PRIMARY KEY(collection, id, version));
                CREATE TABLE IF NOT EXISTS outbox (
                  collection TEXT NOT NULL, id TEXT NOT NULL, version INTEGER NOT NULL,
                  PRIMARY KEY(collection,id));
                CREATE TABLE IF NOT EXISTS sync_base (
                  collection TEXT NOT NULL, id TEXT NOT NULL, hash TEXT NOT NULL, sha TEXT NOT NULL,
                  PRIMARY KEY(collection,id));
                CREATE TABLE IF NOT EXISTS conflicts (
                  collection TEXT NOT NULL, id TEXT NOT NULL, local TEXT NOT NULL, remote TEXT NOT NULL,
                  PRIMARY KEY(collection,id));
            """)
            if migrated:
                db.execute("INSERT OR REPLACE INTO outbox SELECT collection,id,version FROM records")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def validate(collection):
        if collection not in COLLECTIONS:
            raise ValueError(f"collection must be one of {COLLECTIONS}")

    @staticmethod
    def decode(row):
        if row is None:
            raise KeyError("record not found")
        result = dict(row)
        result["data"] = json.loads(result["data"])
        return result

    def get(self, collection, record_id):
        self.validate(collection)
        with self.connection() as db:
            return self.decode(db.execute("SELECT * FROM records WHERE collection=? AND id=?", (collection, record_id)).fetchone())

    def list(self, collection, limit=50, offset=0):
        self.validate(collection)
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("limit must be 1..200 and offset >= 0")
        with self.connection() as db:
            rows = db.execute("SELECT * FROM records WHERE collection=? ORDER BY created_at,id LIMIT ? OFFSET ?", (collection, limit + 1, offset)).fetchall()
            return {"items": [self.decode(r) for r in rows[:limit]], "next_offset": offset + limit if len(rows) > limit else None}

    def put(self, collection, data, record_id=None, expected_version=0, actor="agent", reason="", quota_confirm=False, quota_reason="", _db=None):
        """Create at version 0, or replace a record using its last read version."""
        self.validate(collection)
        if not isinstance(data, dict):
            raise ValueError("data must be a JSON object")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a nonnegative integer")
        encoded = json.dumps(data, ensure_ascii=False, allow_nan=False)
        record_id = record_id or str(uuid4())
        if not isinstance(record_id, str) or not record_id:
            raise ValueError("record_id must be a nonempty string")
        now = datetime.now(timezone.utc).isoformat()
        with (self.connection() if _db is None else nullcontext(_db)) as db:
            if _db is None:
                db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM records WHERE collection=? AND id=?", (collection, record_id)).fetchone()
            current = old["version"] if old else 0
            if expected_version != current:
                raise Conflict(f"version conflict: expected {expected_version}, current {current}; read again before writing")
            if collection == "profiles" and record_id == "company-quotas":
                from .jobs import validate_quotas
                validate_quotas(data)
            if collection == "jobs" and (data.get("company") or (old and json.loads(old["data"]).get("company"))):
                from .jobs import prepare
                data = prepare(db, data, self.decode(old) if old else None, record_id, {"quota_confirm": quota_confirm, "quota_reason": quota_reason, "reason": reason}, Conflict)
                encoded = json.dumps(data, ensure_ascii=False, allow_nan=False)
            version = current + 1
            created = old["created_at"] if old else now
            values = (collection, record_id, version, created, now, encoded)
            db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?,?,?,?)", values)
            db.execute("INSERT INTO history VALUES (?,?,?,?,?,?,?,?)", (*values, actor, reason))
            db.execute("INSERT OR REPLACE INTO outbox VALUES (?,?,?)", (collection, record_id, version))
            return self.decode(db.execute("SELECT * FROM records WHERE collection=? AND id=?", (collection, record_id)).fetchone())

    def history(self, collection, record_id):
        self.validate(collection)
        with self.connection() as db:
            return [self.decode(r) for r in db.execute("SELECT * FROM history WHERE collection=? AND id=? ORDER BY version", (collection, record_id))]

    def export(self):
        """Return one consistent snapshot, including all revision history."""
        with self.connection() as db:
            db.execute("BEGIN")
            return {"schema_version": 1, "records": [self.decode(r) for r in db.execute("SELECT * FROM records ORDER BY collection,id")], "history": [self.decode(r) for r in db.execute("SELECT * FROM history ORDER BY collection,id,version")]}

    def pack(self, collection, record_id):
        with self.connection() as db:
            db.execute("BEGIN")
            record = self.decode(db.execute("SELECT * FROM records WHERE collection=? AND id=?", (collection, record_id)).fetchone())
            history = [self.decode(r) for r in db.execute("SELECT * FROM history WHERE collection=? AND id=? ORDER BY version", (collection, record_id))]
            return {"schema_version": 1, "record": record, "history": history}

    @staticmethod
    def validate_pack(pack):
        if pack.get("schema_version") != 1:
            raise ValueError("unsupported snapshot schema")
        r, history = pack["record"], pack["history"]
        Store.validate(r["collection"])
        if not isinstance(r["id"], str) or not r["id"] or not isinstance(r["data"], dict):
            raise ValueError("invalid record")
        if len(history) != r["version"] or not history:
            raise ValueError("snapshot must contain complete history")
        for version, h in enumerate(history, 1):
            if h["version"] != version or h["id"] != r["id"] or h["collection"] != r["collection"] or not isinstance(h["data"], dict):
                raise ValueError("invalid history chain")
            for key in ("actor", "reason", "created_at", "updated_at"):
                if not isinstance(h[key], str):
                    raise ValueError("invalid history metadata")
        if any(history[-1][key] != r[key] for key in ("data", "created_at", "updated_at", "version")):
            raise ValueError("current record differs from latest history")
        json.dumps(pack, allow_nan=False)

    @staticmethod
    def insert_pack(db, pack):
        r = pack["record"]
        keys = ("collection", "id", "version", "created_at", "updated_at")
        db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?,?,?,?)", (*[r[k] for k in keys], json.dumps(r["data"], ensure_ascii=False)))
        db.execute("DELETE FROM history WHERE collection=? AND id=?", (r["collection"], r["id"]))
        for h in pack["history"]:
            db.execute("INSERT INTO history VALUES (?,?,?,?,?,?,?,?)", (*[h[k] for k in keys], json.dumps(h["data"], ensure_ascii=False), h["actor"], h["reason"]))

    def restore(self, snapshot):
        """Import exact IDs and history; an existing different record is never overwritten."""
        if snapshot.get("schema_version") != 1:
            raise ValueError("unsupported snapshot schema")
        packs = [{"schema_version": 1, "record": r, "history": [h for h in snapshot["history"] if (h["collection"], h["id"]) == (r["collection"], r["id"])]} for r in snapshot["records"]]
        keys = [(p["record"]["collection"], p["record"]["id"]) for p in packs]
        if len(set(keys)) != len(keys) or any((h["collection"], h["id"]) not in keys for h in snapshot["history"]):
            raise ValueError("duplicate record or orphan history in snapshot")
        for pack in packs:
            self.validate_pack(pack)
        imported = 0
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            for pack in packs:
                r = pack["record"]
                existing = db.execute("SELECT * FROM records WHERE collection=? AND id=?", (r["collection"], r["id"])).fetchone()
                if existing:
                    existing_history = [self.decode(h) for h in db.execute("SELECT * FROM history WHERE collection=? AND id=? ORDER BY version", (r["collection"], r["id"]))]
                    if self.decode(existing) != r or existing_history != pack["history"]:
                        raise Conflict("snapshot overlaps an existing record; import into an empty database first")
                    continue
                self.insert_pack(db, pack)
                db.execute("INSERT OR REPLACE INTO outbox VALUES (?,?,?)", (r["collection"], r["id"], r["version"]))
                imported += 1
        return {"imported": imported}
