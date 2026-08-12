from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 3
TERMINAL_STATES = {"promoted", "rejected", "cancelled"}
DEFAULT_MAX_ATTEMPTS = int(os.environ.get("CLARITY_MAX_ATTEMPTS", "5"))

@dataclass(frozen=True)
class Paths:
    root: Path; db: Path; receipts: Path; repairs: Path; artifacts: Path; quarantine: Path; locks: Path; snapshots: Path

def paths(root=None) -> Paths:
    base = Path(root or os.environ.get("CLARITY_HOME") or Path.home()/".clarity").expanduser()
    return Paths(base, base/"clarity.db", base/"receipts.jsonl", base/"receipt-repairs.jsonl", base/"artifacts", base/"quarantine", base/"locks", base/"snapshots")

def utc_ms(): return time.time_ns()//1_000_000
def canonical_json(v): return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",",":"))
def sha256_bytes(data): return hashlib.sha256(data).hexdigest()

def _fsync_dir(path:Path):
    fd=os.open(path,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)

def _atomic_bytes(path:Path,data:bytes):
    path.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(prefix=f".{path.name}.",dir=path.parent)
    try:
        with os.fdopen(fd,"wb") as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path);_fsync_dir(path.parent)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

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
        CREATE TABLE IF NOT EXISTS missions(id TEXT PRIMARY KEY,kind TEXT NOT NULL,spec_json TEXT NOT NULL,spec_sha256 TEXT NOT NULL,state TEXT NOT NULL,created_ms INTEGER NOT NULL,updated_ms INTEGER NOT NULL,attempt INTEGER NOT NULL DEFAULT 0,max_attempts INTEGER NOT NULL DEFAULT 5,lease_owner TEXT,lease_until_ms INTEGER,lease_token TEXT,idempotency_key TEXT UNIQUE,last_error TEXT);
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL UNIQUE,mission_id TEXT,type TEXT NOT NULL,payload_json TEXT NOT NULL,created_ms INTEGER NOT NULL,prev_hash TEXT,event_hash TEXT NOT NULL UNIQUE,FOREIGN KEY(mission_id) REFERENCES missions(id));
        CREATE TABLE IF NOT EXISTS artifacts(sha256 TEXT PRIMARY KEY,mission_id TEXT NOT NULL,logical_name TEXT NOT NULL,relative_path TEXT NOT NULL,byte_count INTEGER NOT NULL,promoted_ms INTEGER NOT NULL,FOREIGN KEY(mission_id) REFERENCES missions(id));
        CREATE INDEX IF NOT EXISTS idx_missions_state ON missions(state,created_ms); CREATE INDEX IF NOT EXISTS idx_events_mission ON events(mission_id,seq);
        """)
        cols={r[1] for r in self.conn.execute("PRAGMA table_info(missions)")}
        if "lease_token" not in cols:self.conn.execute("ALTER TABLE missions ADD COLUMN lease_token TEXT")
        if "max_attempts" not in cols:self.conn.execute(f"ALTER TABLE missions ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT {DEFAULT_MAX_ATTEMPTS}")
        self.conn.execute("INSERT INTO meta(key,value) VALUES('schema_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(SCHEMA_VERSION),))
    def _last_event_hash(self):
        r=self.conn.execute("SELECT event_hash FROM events ORDER BY seq DESC LIMIT 1").fetchone();return r[0] if r else None
    def _receipt_from_row(self,r):
        return {"event_id":r["event_id"],"mission_id":r["mission_id"],"type":r["type"],"payload":json.loads(r["payload_json"]),"created_ms":r["created_ms"],"prev_hash":r["prev_hash"],"event_hash":r["event_hash"]}
    def append_event(self,event_type,payload,mission_id=None):
        created=utc_ms();event_id=str(uuid.uuid4());prev=self._last_event_hash();body={"event_id":event_id,"mission_id":mission_id,"type":event_type,"payload":payload,"created_ms":created,"prev_hash":prev};h=sha256_bytes(canonical_json(body).encode());receipt={**body,"event_hash":h}
        with self.conn:self.conn.execute("INSERT INTO events(event_id,mission_id,type,payload_json,created_ms,prev_hash,event_hash) VALUES(?,?,?,?,?,?,?)",(event_id,mission_id,event_type,canonical_json(payload),created,prev,h))
        fd=os.open(self.paths.receipts,os.O_APPEND|os.O_CREAT|os.O_WRONLY,0o600)
        try:os.write(fd,(canonical_json(receipt)+"\n").encode());os.fsync(fd)
        finally:os.close(fd)
        return receipt
    def add_mission(self,kind,spec,idempotency_key=None,max_attempts=None):
        sj=canonical_json(spec);sh=sha256_bytes(sj.encode());now=utc_ms();mid=str(uuid.uuid4());budget=max(1,int(max_attempts or DEFAULT_MAX_ATTEMPTS))
        try:
            with self.conn:self.conn.execute("INSERT INTO missions(id,kind,spec_json,spec_sha256,state,created_ms,updated_ms,max_attempts,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?)",(mid,kind,sj,sh,"queued",now,now,budget,idempotency_key))
        except sqlite3.IntegrityError:
            if not idempotency_key:raise
            r=self.conn.execute("SELECT id,kind,spec_sha256 FROM missions WHERE idempotency_key=?",(idempotency_key,)).fetchone()
            if not r:raise
            if r["kind"]!=kind or r["spec_sha256"]!=sh:raise RuntimeError("idempotency conflict: key already names different work")
            return str(r["id"])
        self.append_event("mission.queued",{"kind":kind,"spec_sha256":sh,"max_attempts":budget},mid);return mid
    def lease_next(self,owner,ttl_ms=120000):
        now=utc_ms();until=now+ttl_ms;token=str(uuid.uuid4())
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            r=self.conn.execute("SELECT id FROM missions WHERE state='queued' OR (state='working' AND lease_until_ms IS NOT NULL AND lease_until_ms < ?) ORDER BY created_ms LIMIT 1",(now,)).fetchone()
            if not r:self.conn.execute("COMMIT");return None
            mid=r["id"];current=self.conn.execute("SELECT attempt,max_attempts FROM missions WHERE id=?",(mid,)).fetchone()
            if current["attempt"]>=current["max_attempts"]:
                self.conn.execute("UPDATE missions SET state='blocked',updated_ms=?,last_error='attempt budget exhausted',lease_owner=NULL,lease_until_ms=NULL,lease_token=NULL WHERE id=?",(now,mid));self.conn.execute("COMMIT")
                self.append_event("mission.blocked",{"reason":"attempt_budget_exhausted","attempt":current["attempt"],"max_attempts":current["max_attempts"]},mid);return self.lease_next(owner,ttl_ms)
            cur=self.conn.execute("UPDATE missions SET state='working',lease_owner=?,lease_until_ms=?,lease_token=?,updated_ms=?,attempt=attempt+1 WHERE id=? AND (state='queued' OR (state='working' AND lease_until_ms < ?))",(owner,until,token,now,mid,now))
            if cur.rowcount!=1:self.conn.execute("ROLLBACK");return None
            self.conn.execute("COMMIT")
        except BaseException:
            if self.conn.in_transaction:self.conn.execute("ROLLBACK")
            raise
        self.append_event("mission.leased",{"owner":owner,"lease_until_ms":until,"lease_token":token},mid);return self.get_mission(mid)
    def assert_lease(self,mission_id,lease_token):
        r=self.get_mission(mission_id);now=utc_ms()
        if not r or r["state"]!="working" or r["lease_token"]!=lease_token or not r["lease_until_ms"] or r["lease_until_ms"]<now:raise RuntimeError("lease lost or expired")
    def transition(self,mission_id,new_state,*,lease_token=None,error=None,payload=None):
        r=self.get_mission(mission_id)
        if not r:raise KeyError(mission_id)
        if r["state"] in TERMINAL_STATES:raise RuntimeError("terminal mission cannot transition")
        if r["state"]=="working":self.assert_lease(mission_id,lease_token)
        old=r["state"];now=utc_ms()
        with self.conn:self.conn.execute("UPDATE missions SET state=?,updated_ms=?,last_error=?,lease_owner=NULL,lease_until_ms=NULL,lease_token=NULL WHERE id=?",(new_state,now,error,mission_id))
        self.append_event(f"mission.{new_state}",{"from":old,"error":error,**(payload or {})},mission_id)
    def requeue_blocked(self,mission_id,*,new_max_attempts=None):
        r=self.get_mission(mission_id)
        if not r or r["state"]!="blocked":raise RuntimeError("mission is not blocked")
        budget=max(r["max_attempts"]+1,int(new_max_attempts or 0))
        with self.conn:self.conn.execute("UPDATE missions SET state='queued',max_attempts=?,updated_ms=?,last_error=NULL WHERE id=?",(budget,utc_ms(),mission_id))
        self.append_event("mission.requeued",{"max_attempts":budget},mission_id)
    def get_mission(self,mid):return self.conn.execute("SELECT * FROM missions WHERE id=?",(mid,)).fetchone()
    def list_missions(self):return list(self.conn.execute("SELECT * FROM missions ORDER BY created_ms DESC"))
    def recover_expired(self):
        now=utc_ms();rows=list(self.conn.execute("SELECT id,attempt,max_attempts FROM missions WHERE state='working' AND lease_until_ms < ?",(now,)));recovered=0
        for r in rows:
            state="blocked" if r["attempt"]>=r["max_attempts"] else "queued";reason="attempt_budget_exhausted" if state=="blocked" else "expired_lease"
            with self.conn:self.conn.execute("UPDATE missions SET state=?,lease_owner=NULL,lease_until_ms=NULL,lease_token=NULL,updated_ms=?,last_error=? WHERE id=?",(state,now,reason,r["id"]))
            self.append_event(f"mission.{state if state=='blocked' else 'recovered'}",{"reason":reason,"attempt":r["attempt"],"max_attempts":r["max_attempts"]},r["id"]);recovered+=1
        return recovered
    def verify_chain(self):
        prev=None
        for r in self.conn.execute("SELECT * FROM events ORDER BY seq"):
            body={"event_id":r["event_id"],"mission_id":r["mission_id"],"type":r["type"],"payload":json.loads(r["payload_json"]),"created_ms":r["created_ms"],"prev_hash":r["prev_hash"]};expected=sha256_bytes(canonical_json(body).encode())
            if r["prev_hash"]!=prev:return False,f"broken prev_hash at event seq {r['seq']}"
            if r["event_hash"]!=expected:return False,f"broken event_hash at event seq {r['seq']}"
            prev=r["event_hash"]
        return True,"ok"
    def verify_receipts(self):
        db=[self._receipt_from_row(r) for r in self.conn.execute("SELECT * FROM events ORDER BY seq")]
        if not self.paths.receipts.exists():return (not db,"missing receipts.jsonl" if db else "ok")
        try:lines=[json.loads(x) for x in self.paths.receipts.read_text("utf-8").splitlines() if x.strip()]
        except Exception as e:return False,f"receipt parse failure: {e}"
        if lines!=db:return False,"receipt/db divergence"
        return True,"ok"
    def rebuild_receipts(self):
        ok,why=self.verify_chain()
        if not ok:raise RuntimeError(f"refuse receipt rebuild: {why}")
        before=sha256_bytes(self.paths.receipts.read_bytes()) if self.paths.receipts.exists() else None
        rows=[self._receipt_from_row(r) for r in self.conn.execute("SELECT * FROM events ORDER BY seq")];data=b"".join((canonical_json(r)+"\n").encode() for r in rows);_atomic_bytes(self.paths.receipts,data)
        repair={"schema":"clarity.receipt-repair.v1","at_ms":utc_ms(),"before_sha256":before,"after_sha256":sha256_bytes(data),"source":"sqlite-verified-event-chain","event_count":len(rows)}
        fd=os.open(self.paths.repairs,os.O_APPEND|os.O_CREAT|os.O_WRONLY,0o600)
        try:os.write(fd,(canonical_json(repair)+"\n").encode());os.fsync(fd)
        finally:os.close(fd)
        return len(rows)
    def create_snapshot(self,label=None):
        self.checkpoint();stamp=f"{utc_ms()}-{label or 'snapshot'}";target=self.paths.snapshots/stamp;target.mkdir(parents=True,exist_ok=False)
        dbcopy=target/"clarity.db";dst=sqlite3.connect(dbcopy)
        try:self.conn.backup(dst)
        finally:dst.close()
        receipts=(self.paths.receipts.read_bytes() if self.paths.receipts.exists() else b"");_atomic_bytes(target/"receipts.jsonl",receipts)
        artifact_entries=[];artifact_root=target/"artifacts"
        for r in self.conn.execute("SELECT sha256,relative_path FROM artifacts ORDER BY sha256"):
            src=self.paths.root/r["relative_path"]
            if not src.exists() or sha256_bytes(src.read_bytes())!=r["sha256"]:raise RuntimeError(f"cannot snapshot missing/corrupt artifact: {r['relative_path']}")
            rel=Path(r["relative_path"]).relative_to("artifacts");dstp=artifact_root/rel;dstp.parent.mkdir(parents=True,exist_ok=True)
            try:os.link(src,dstp)
            except OSError:shutil.copy2(src,dstp)
            artifact_entries.append({"relative_path":str(rel),"sha256":r["sha256"],"byte_count":src.stat().st_size})
        manifest={"schema":"clarity.snapshot.v2","created_ms":utc_ms(),"db_sha256":sha256_bytes(dbcopy.read_bytes()),"receipts_sha256":sha256_bytes(receipts),"artifacts":artifact_entries};_atomic_bytes(target/"manifest.json",(canonical_json(manifest)+"\n").encode());return target
    @staticmethod
    def restore_snapshot(root,snapshot):
        root=Path(root);snap=Path(snapshot);manifest=json.loads((snap/"manifest.json").read_text());db=(snap/"clarity.db").read_bytes();receipts=(snap/"receipts.jsonl").read_bytes()
        if manifest.get("schema")!="clarity.snapshot.v2" or sha256_bytes(db)!=manifest["db_sha256"] or sha256_bytes(receipts)!=manifest["receipts_sha256"]:raise RuntimeError("snapshot manifest/hash mismatch")
        for entry in manifest.get("artifacts",[]):
            src=snap/"artifacts"/entry["relative_path"]
            if not src.exists() or sha256_bytes(src.read_bytes())!=entry["sha256"]:raise RuntimeError(f"snapshot artifact mismatch: {entry['relative_path']}")
        root.mkdir(parents=True,exist_ok=True)
        for stale in (root/"clarity.db-wal",root/"clarity.db-shm"):stale.unlink(missing_ok=True)
        _atomic_bytes(root/"clarity.db",db);_atomic_bytes(root/"receipts.jsonl",receipts)
        for entry in manifest.get("artifacts",[]):
            src=snap/"artifacts"/entry["relative_path"];dst=root/"artifacts"/entry["relative_path"];_atomic_bytes(dst,src.read_bytes())
        _fsync_dir(root)
    def orphan_artifacts(self):
        referenced={str((self.paths.root/r[0]).resolve()) for r in self.conn.execute("SELECT relative_path FROM artifacts")};return [p for p in self.paths.artifacts.rglob("*") if p.is_file() and str(p.resolve()) not in referenced]
    def gc_orphan_artifacts(self):
        orphans=self.orphan_artifacts()
        for p in orphans:p.unlink(missing_ok=True)
        for p in sorted(self.paths.artifacts.rglob("*"),reverse=True):
            if p.is_dir():
                try:p.rmdir()
                except OSError:pass
        return len(orphans)
    def integrity_check(self):
        v=self.conn.execute("PRAGMA integrity_check").fetchone()[0];return v=="ok",v
    def checkpoint(self):self.conn.execute("PRAGMA wal_checkpoint(FULL)")
    def rows_as_dicts(self,rows:Iterable[sqlite3.Row]):return [dict(r) for r in rows]
