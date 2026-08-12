from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1
TERMINAL_STATES = {"promoted", "rejected", "cancelled"}


@dataclass(frozen=True)
class Paths:
    root: Path
    db: Path
    receipts: Path
    artifacts: Path
    quarantine: Path
    locks: Path
    snapshots: Path


def paths(root: str | os.PathLike[str] | None = None) -> Paths:
    base = Path(root or os.environ.get("CLARITY_HOME") or Path.home() / ".clarity").expanduser()
    return Paths(
        root=base,
        db=base / "clarity.db",
        receipts=base / "receipts.jsonl",
        artifacts=base / "artifacts",
        quarantine=base / "quarantine",
        locks=base / "locks",
        snapshots=base / "snapshots",
    )


def utc_ms() -> int:
    return time.time_ns() // 1_000_000


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Store:
    def __init__(self, root: str | os.PathLike[str] | None = None):
        self.paths = paths(root)
        self.paths.root.mkdir(parents=True, exist_ok=True)
        for p in (self.paths.artifacts, self.paths.quarantine, self.paths.locks, self.paths.snapshots):
            p.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.paths.db, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    def _migrate(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta(
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS missions(
              id TEXT PRIMARY KEY,
              kind TEXT NOT NULL,
              spec_json TEXT NOT NULL,
              spec_sha256 TEXT NOT NULL,
              state TEXT NOT NULL,
              created_ms INTEGER NOT NULL,
              updated_ms INTEGER NOT NULL,
              attempt INTEGER NOT NULL DEFAULT 0,
              lease_owner TEXT,
              lease_until_ms INTEGER,
              idempotency_key TEXT UNIQUE,
              last_error TEXT
            );
            CREATE TABLE IF NOT EXISTS events(
              seq INTEGER PRIMARY KEY AUTOINCREMENT,
              event_id TEXT NOT NULL UNIQUE,
              mission_id TEXT,
              type TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              created_ms INTEGER NOT NULL,
              prev_hash TEXT,
              event_hash TEXT NOT NULL UNIQUE,
              FOREIGN KEY(mission_id) REFERENCES missions(id)
            );
            CREATE TABLE IF NOT EXISTS artifacts(
              sha256 TEXT PRIMARY KEY,
              mission_id TEXT NOT NULL,
              logical_name TEXT NOT NULL,
              relative_path TEXT NOT NULL,
              byte_count INTEGER NOT NULL,
              promoted_ms INTEGER NOT NULL,
              FOREIGN KEY(mission_id) REFERENCES missions(id)
            );
            CREATE INDEX IF NOT EXISTS idx_missions_state ON missions(state, created_ms);
            CREATE INDEX IF NOT EXISTS idx_events_mission ON events(mission_id, seq);
            """
        )
        self.conn.execute(
            "INSERT INTO meta(key,value) VALUES('schema_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )

    def _last_event_hash(self) -> str | None:
        row = self.conn.execute("SELECT event_hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def append_event(self, event_type: str, payload: dict[str, Any], mission_id: str | None = None) -> dict[str, Any]:
        created = utc_ms()
        event_id = str(uuid.uuid4())
        prev_hash = self._last_event_hash()
        body = {
            "event_id": event_id,
            "mission_id": mission_id,
            "type": event_type,
            "payload": payload,
            "created_ms": created,
            "prev_hash": prev_hash,
        }
        event_hash = sha256_bytes(canonical_json(body).encode())
        receipt = {**body, "event_hash": event_hash}
        with self.conn:
            self.conn.execute(
                "INSERT INTO events(event_id,mission_id,type,payload_json,created_ms,prev_hash,event_hash) VALUES(?,?,?,?,?,?,?)",
                (event_id, mission_id, event_type, canonical_json(payload), created, prev_hash, event_hash),
            )
        # Independent append-only audit stream. fsync makes acknowledged receipts survive ordinary process death.
        line = (canonical_json(receipt) + "\n").encode()
        fd = os.open(self.paths.receipts, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)
        return receipt

    def add_mission(self, kind: str, spec: dict[str, Any], idempotency_key: str | None = None) -> str:
        spec_json = canonical_json(spec)
        spec_hash = sha256_bytes(spec_json.encode())
        now = utc_ms()
        mission_id = str(uuid.uuid4())
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO missions(id,kind,spec_json,spec_sha256,state,created_ms,updated_ms,idempotency_key) VALUES(?,?,?,?,?,?,?,?)",
                    (mission_id, kind, spec_json, spec_hash, "queued", now, now, idempotency_key),
                )
        except sqlite3.IntegrityError:
            if not idempotency_key:
                raise
            row = self.conn.execute("SELECT id FROM missions WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if not row:
                raise
            return str(row[0])
        self.append_event("mission.queued", {"kind": kind, "spec_sha256": spec_hash}, mission_id)
        return mission_id

    def lease_next(self, owner: str, ttl_ms: int = 120_000) -> sqlite3.Row | None:
        now = utc_ms()
        until = now + ttl_ms
        with self.conn:
            row = self.conn.execute(
                """
                SELECT * FROM missions
                WHERE state='queued' OR (state='working' AND lease_until_ms IS NOT NULL AND lease_until_ms < ?)
                ORDER BY created_ms ASC LIMIT 1
                """,
                (now,),
            ).fetchone()
            if not row:
                return None
            mission_id = row["id"]
            self.conn.execute(
                "UPDATE missions SET state='working',lease_owner=?,lease_until_ms=?,updated_ms=?,attempt=attempt+1 WHERE id=?",
                (owner, until, now, mission_id),
            )
        self.append_event("mission.leased", {"owner": owner, "lease_until_ms": until}, mission_id)
        return self.get_mission(mission_id)

    def transition(self, mission_id: str, new_state: str, *, error: str | None = None, payload: dict[str, Any] | None = None) -> None:
        row = self.get_mission(mission_id)
        if not row:
            raise KeyError(mission_id)
        old_state = row["state"]
        if old_state in TERMINAL_STATES:
            raise RuntimeError(f"terminal mission cannot transition: {old_state} -> {new_state}")
        now = utc_ms()
        with self.conn:
            self.conn.execute(
                "UPDATE missions SET state=?,updated_ms=?,last_error=?,lease_owner=NULL,lease_until_ms=NULL WHERE id=?",
                (new_state, now, error, mission_id),
            )
        self.append_event(
            f"mission.{new_state}",
            {"from": old_state, "error": error, **(payload or {})},
            mission_id,
        )

    def get_mission(self, mission_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone()

    def list_missions(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM missions ORDER BY created_ms DESC"))

    def recover_expired(self) -> int:
        now = utc_ms()
        rows = list(self.conn.execute("SELECT id FROM missions WHERE state='working' AND lease_until_ms < ?", (now,)))
        for row in rows:
            mission_id = row[0]
            with self.conn:
                self.conn.execute(
                    "UPDATE missions SET state='queued',lease_owner=NULL,lease_until_ms=NULL,updated_ms=?,last_error='expired lease recovered' WHERE id=?",
                    (now, mission_id),
                )
            self.append_event("mission.recovered", {"reason": "expired_lease"}, mission_id)
        return len(rows)

    def verify_chain(self) -> tuple[bool, str]:
        prev: str | None = None
        for row in self.conn.execute("SELECT * FROM events ORDER BY seq ASC"):
            body = {
                "event_id": row["event_id"],
                "mission_id": row["mission_id"],
                "type": row["type"],
                "payload": json.loads(row["payload_json"]),
                "created_ms": row["created_ms"],
                "prev_hash": row["prev_hash"],
            }
            expected = sha256_bytes(canonical_json(body).encode())
            if row["prev_hash"] != prev:
                return False, f"broken prev_hash at event seq {row['seq']}"
            if row["event_hash"] != expected:
                return False, f"broken event_hash at event seq {row['seq']}"
            prev = row["event_hash"]
        return True, "ok"

    def integrity_check(self) -> tuple[bool, str]:
        value = self.conn.execute("PRAGMA integrity_check").fetchone()[0]
        return value == "ok", value

    def checkpoint(self) -> None:
        self.conn.execute("PRAGMA wal_checkpoint(FULL)")

    def rows_as_dicts(self, rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
        return [dict(r) for r in rows]
