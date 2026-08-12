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

SCHEMA_VERSION = 2
TERMINAL_STATES = {"promoted", "rejected", "cancelled"}

@dataclass(frozen=True)
class Paths:
    root: Path; db: Path; receipts: Path; artifacts: Path; quarantine: Path; locks: Path; snapshots: Path

def paths(root=None) -> Paths:
    base = Path(root or os.environ.get("CLARITY_HOME") or Path.home()/".clarity").expanduser()
    return Paths(base, base/"clarity.db", base/"receipts.jsonl", base/"artifacts", base/"quarantine", base/"locks", base/"snapshots")

def utc_ms(): return time.time_ns()//1_000_000
def canonical_json(v): return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",",":"))
def sha256_bytes(data): return hashlib.sha256(data).hexdigest()

class Store:
    def __init__(self, root=None):
        self.paths=paths(root); self.paths.root.mkdir(parents=True,exist_ok=True)
        for p in (self.paths.artifacts,self.paths.quarantine,self.paths.locks,self.paths.snapshots): p.mkdir(parents=True,exist_ok=True)
        self.conn=sqlite3.connect(self.paths.db,timeout=30,isolation_level=None); self.conn.row_factory=sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL"); self.conn.execute("PRAGMA synchronous=FULL"); self.conn.execute("PRAGMA foreign_keys=ON"); self.conn.execute("PRAGMA busy_timeout=30000")
        self._migrate()
    def close(self): self.conn.close()
    def _migrate(self):
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS missions(id TEXT PRIMARY KEY,kind TEXT NOT NULL,spec_json TEXT NOT NULL,spec_sha256 TEXT NOT NULL,state TEXT NOT NULL,created_ms INTEGER NOT NULL,updated_ms INTEGER NOT NULL,attempt INTEGER NOT NULL DEFAULT 0,lease_owner TEXT,lease_until_ms INTEGER,lease_token TEXT,idempotency_key TEXT UNIQUE,last_error TEXT);
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL UNIQUE,mission_id TEXT,type TEXT NOT NULL,payload_json TEXT NOT NULL,created_ms INTEGER NOT NULL,prev_hash TEXT,event_hash TEXT NOT NULL UNIQUE,FOREIGN KEY(mission_id) REFERENCES missions(id));
        CREATE TABLE IF NOT EXISTS artifacts(sha256 TEXT PRIMARY KEY,mission_id TEXT NOT NULL,logical_name TEXT NOT NULL,relative_path TEXT NOT NULL,byte_count INTEGER NOT NULL,promoted_ms INTEGER NOT NULL,FOREIGN KEY(mission_id) REFERENCES missions(id));
        CREATE INDEX IF NOT EXISTS idx_missions_state ON missions(state,created_ms); CREATE INDEX IF NOT EXISTS idx_events_mission ON events(mission_id,seq);
        """)
        cols={r[1] for r in self.conn.execute("PRAGMA table_info(missions)")}
        if "lease_token" not in cols: self.conn.execute("ALTER TABLE missions ADD COLUMN lease_token TEXT")
        self.conn.execute("INSERT INTO meta(key,value) VALUES('schema_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(SCHEMA_VERSION),))
    def _last_event_hash(self):
        r=self.conn.execute("SELECT event_hash FROM events ORDER BY seq DESC LIMIT 1").fetchone(); return r[0] if r else None
    def append_event(self,event_type,payload,mission_id=None):
        created=utc_ms(); event_id=str(uuid.uuid4()); prev=self._last_event_hash(); body={"event_id":event_id,"mission_id":mission_id,"type":event_type,"payload":payload,"created_ms":created,"prev_hash":prev}; h=sha256_bytes(canonical_json(body).encode()); receipt={**body,"event_hash":h}
        with self.conn: self.conn.execute("INSERT INTO events(event_id,mission_id,type,payload_json,created_ms,prev_hash,event_hash) VALUES(?,?,?,?,?,?,?)",(event_id,mission_id,event_type,canonical_json(payload),created,prev,h))
        fd=os.open(self.paths.receipts,os.O_APPEND|os.O_CREAT|os.O_WRONLY,0o600)
        try: os.write(fd,(canonical_json(receipt)+"\n").encode()); os.fsync(fd)
        finally: os.close(fd)
        return receipt
    def add_mission(self,kind,spec,idempotency_key=None):
        sj=canonical_json(spec); sh=sha256_bytes(sj.encode()); now=utc_ms(); mid=str(uuid.uuid4())
        try:
            with self.conn: self.conn.execute("INSERT INTO missions(id,kind,spec_json,spec_sha256,state,created_ms,updated_ms,idempotency_key) VALUES(?,?,?,?,?,?,?,?)",(mid,kind,sj,sh,"queued",now,now,idempotency_key))
        except sqlite3.IntegrityError:
            if not idempotency_key: raise
            r=self.conn.execute("SELECT id,kind,spec_sha256 FROM missions WHERE idempotency_key=?",(idempotency_key,)).fetchone()
            if not r: raise
            if r["kind"]!=kind or r["spec_sha256"]!=sh: raise RuntimeError("idempotency conflict: key already names different work")
            return str(r["id"])
        self.append_event("mission.queued",{"kind":kind,"spec_sha256":sh},mid); return mid
    def lease_next(self,owner,ttl_ms=120000):
        now=utc_ms(); until=now+ttl_ms; token=str(uuid.uuid4())
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            r=self.conn.execute("SELECT id FROM missions WHERE state='queued' OR (state='working' AND lease_until_ms IS NOT NULL AND lease_until_ms < ?) ORDER BY created_ms LIMIT 1",(now,)).fetchone()
            if not r: self.conn.execute("COMMIT"); return None
            mid=r["id"]
            cur=self.conn.execute("UPDATE missions SET state='working',lease_owner=?,lease_until_ms=?,lease_token=?,updated_ms=?,attempt=attempt+1 WHERE id=? AND (state='queued' OR (state='working' AND lease_until_ms < ?))",(owner,until,token,now,mid,now))
            if cur.rowcount!=1: self.conn.execute("ROLLBACK"); return None
            self.conn.execute("COMMIT")
        except BaseException:
            if self.conn.in_transaction: self.conn.execute("ROLLBACK")
            raise
        self.append_event("mission.leased",{"owner":owner,"lease_until_ms":until,"lease_token":token},mid); return self.get_mission(mid)
    def assert_lease(self,mission_id,lease_token):
        r=self.get_mission(mission_id); now=utc_ms()
        if not r or r["state"]!="working" or r["lease_token"]!=lease_token or not r["lease_until_ms"] or r["lease_until_ms"]<now: raise RuntimeError("lease lost or expired")
    def transition(self,mission_id,new_state,*,lease_token=None,error=None,payload=None):
        r=self.get_mission(mission_id)
        if not r: raise KeyError(mission_id)
        if r["state"] in TERMINAL_STATES: raise RuntimeError("terminal mission cannot transition")
        if r["state"]=="working": self.assert_lease(mission_id,lease_token)
        old=r["state"]; now=utc_ms()
        with self.conn: self.conn.execute("UPDATE missions SET state=?,updated_ms=?,last_error=?,lease_owner=NULL,lease_until_ms=NULL,lease_token=NULL WHERE id=?",(new_state,now,error,mission_id))
        self.append_event(f"mission.{new_state}",{"from":old,"error":error,**(payload or {})},mission_id)
    def get_mission(self,mid): return self.conn.execute("SELECT * FROM missions WHERE id=?",(mid,)).fetchone()
    def list_missions(self): return list(self.conn.execute("SELECT * FROM missions ORDER BY created_ms DESC"))
    def recover_expired(self):
        now=utc_ms(); rows=list(self.conn.execute("SELECT id FROM missions WHERE state='working' AND lease_until_ms < ?",(now,)))
        for r in rows:
            with self.conn: self.conn.execute("UPDATE missions SET state='queued',lease_owner=NULL,lease_until_ms=NULL,lease_token=NULL,updated_ms=?,last_error='expired lease recovered' WHERE id=?",(now,r[0]))
            self.append_event("mission.recovered",{"reason":"expired_lease"},r[0])
        return len(rows)
    def verify_chain(self):
        prev=None
        for r in self.conn.execute("SELECT * FROM events ORDER BY seq"):
            body={"event_id":r["event_id"],"mission_id":r["mission_id"],"type":r["type"],"payload":json.loads(r["payload_json"]),"created_ms":r["created_ms"],"prev_hash":r["prev_hash"]}; expected=sha256_bytes(canonical_json(body).encode())
            if r["prev_hash"]!=prev: return False,f"broken prev_hash at event seq {r['seq']}"
            if r["event_hash"]!=expected: return False,f"broken event_hash at event seq {r['seq']}"
            prev=r["event_hash"]
        return True,"ok"
    def verify_receipts(self):
        db=[dict(r) for r in self.conn.execute("SELECT event_id,event_hash FROM events ORDER BY seq")]
        if not self.paths.receipts.exists(): return (not db,"missing receipts.jsonl" if db else "ok")
        try: lines=[json.loads(x) for x in self.paths.receipts.read_text("utf-8").splitlines() if x.strip()]
        except Exception as e: return False,f"receipt parse failure: {e}"
        if len(lines)!=len(db): return False,f"receipt/db count mismatch {len(lines)} != {len(db)}"
        prev=None
        for i,(rec,dbr) in enumerate(zip(lines,db),1):
            body={k:rec.get(k) for k in ("event_id","mission_id","type","payload","created_ms","prev_hash")}; expected=sha256_bytes(canonical_json(body).encode())
            if rec.get("prev_hash")!=prev or rec.get("event_hash")!=expected: return False,f"broken receipt chain at line {i}"
            if rec.get("event_id")!=dbr["event_id"] or rec.get("event_hash")!=dbr["event_hash"]: return False,f"receipt/db divergence at line {i}"
            prev=rec["event_hash"]
        return True,"ok"
    def integrity_check(self):
        v=self.conn.execute("PRAGMA integrity_check").fetchone()[0]; return v=="ok",v
    def checkpoint(self): self.conn.execute("PRAGMA wal_checkpoint(FULL)")
    def rows_as_dicts(self,rows:Iterable[sqlite3.Row]): return [dict(r) for r in rows]
