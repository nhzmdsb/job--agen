"""Transactional JSON storage. Business interpretation belongs to the agent."""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

COLLECTIONS = ("sources", "leads", "jobs", "runs", "artifacts", "profiles")


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get("JOB_AGENT_DB", "data/workspace.sqlite3")).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
            """)

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

    def put(self, collection, data, record_id=None, expected_version=0, actor="agent", reason=""):
        """Create at version 0, or replace a record using its last read version."""
        self.validate(collection)
        if not isinstance(data, dict):
            raise ValueError("data must be a JSON object")
        if not isinstance(expected_version, int) or expected_version < 0:
            raise ValueError("expected_version must be a nonnegative integer")
        encoded = json.dumps(data, ensure_ascii=False, allow_nan=False)
        record_id = record_id or str(uuid4())
        if not isinstance(record_id, str) or not record_id:
            raise ValueError("record_id must be a nonempty string")
        now = datetime.now(timezone.utc).isoformat()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM records WHERE collection=? AND id=?", (collection, record_id)).fetchone()
            current = old["version"] if old else 0
            if expected_version != current:
                raise Conflict(f"version conflict: expected {expected_version}, current {current}; read again before writing")
            version = current + 1
            created = old["created_at"] if old else now
            values = (collection, record_id, version, created, now, encoded)
            db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?,?,?,?)", values)
            db.execute("INSERT INTO history VALUES (?,?,?,?,?,?,?,?)", (*values, actor, reason))
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
