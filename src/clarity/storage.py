from __future__ import annotations

import hashlib,json,os,shutil,sqlite3,tempfile,time,uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any,Iterable

SCHEMA_VERSION=5
TERMINAL_STATES={"promoted","rejected","cancelled"}
DEFAULT_MAX_ATTEMPTS=int(os.environ.get("CLARITY_MAX_ATTEMPTS","5"))

@dataclass(frozen=True)
class Paths:
    root:Path;db:Path;receipts:Path;repairs:Path;artifacts:Path;quarantine:Path;locks:Path;snapshots:Path

def paths(root=None):
    b=Path(root or os.environ.get("CLARITY_HOME") or Path.home()/".clarity").expanduser();return Paths(b,b/"clarity.db",b/"receipts.jsonl",b/"receipt-repairs.jsonl",b/"artifacts",b/"quarantine",b/"locks",b/"snapshots")
def utc_ms():return time.time_ns()//1_000_000
def canonical_json(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":"))
def sha256_bytes(data):return hashlib.sha256(data).hexdigest()
def _fsync_dir(p):
    fd=os.open(p,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
def _atomic_bytes(p,data):
    p.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(prefix=f".{p.name}.",dir=p.parent)
    try:
        with os.fdopen(fd,"wb") as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(tmp,p);_fsync_dir(p.parent)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

class Store:
    def __init__(self,root=None):
        self.paths=paths(root);self.paths.root.mkdir(parents=True,exist_ok=True)
        for p in (self.paths.artifacts,self.paths.quarantine,self.paths.locks,self.paths.snapshots):p.mkdir(parents=True,exist_ok=True)
        self.conn=sqlite3.connect(self.paths.db,timeout=30,isolation_level=None);self.conn.row_factory=sqlite3.Row
        for q in ("PRAGMA journal_mode=WAL","PRAGMA synchronous=FULL","PRAGMA foreign_keys=ON","PRAGMA busy_timeout=30000"):self.conn.execute(q)
        self._migrate()
    def close(self):self.conn.close()
    def _migrate(self):
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS missions(id TEXT PRIMARY KEY,kind TEXT NOT NULL,spec_json TEXT NOT NULL,spec_sha256 TEXT NOT NULL,state TEXT NOT NULL,created_ms INTEGER NOT NULL,updated_ms INTEGER NOT NULL,attempt INTEGER NOT NULL DEFAULT 0,max_attempts INTEGER NOT NULL DEFAULT 5,lease_owner TEXT,lease_until_ms INTEGER,lease_token TEXT,idempotency_key TEXT UNIQUE,last_error TEXT);
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL UNIQUE,mission_id TEXT,type TEXT NOT NULL,payload_json TEXT NOT NULL,created_ms INTEGER NOT NULL,prev_hash TEXT,event_hash TEXT NOT NULL UNIQUE,FOREIGN KEY(mission_id) REFERENCES missions(id));
        CREATE TABLE IF NOT EXISTS artifacts(sha256 TEXT PRIMARY KEY,mission_id TEXT NOT NULL,logical_name TEXT NOT NULL,relative_path TEXT NOT NULL,byte_count INTEGER NOT NULL,promoted_ms INTEGER NOT NULL,FOREIGN KEY(mission_id) REFERENCES missions(id));
        CREATE TABLE IF NOT EXISTS mission_artifacts(mission_id TEXT NOT NULL,sha256 TEXT NOT NULL,logical_name TEXT NOT NULL,relative_path TEXT NOT NULL,byte_count INTEGER NOT NULL,promoted_ms INTEGER NOT NULL,PRIMARY KEY(mission_id,logical_name),FOREIGN KEY(mission_id) REFERENCES missions(id));
        CREATE INDEX IF NOT EXISTS idx_missions_state ON missions(state,created_ms);CREATE INDEX IF NOT EXISTS idx_events_mission ON events(mission_id,seq);CREATE INDEX IF NOT EXISTS idx_mission_artifacts_sha ON mission_artifacts(sha256);
        INSERT OR IGNORE INTO mission_artifacts(mission_id,sha256,logical_name,relative_path,byte_count,promoted_ms) SELECT mission_id,sha256,logical_name,relative_path,byte_count,promoted_ms FROM artifacts;
        """)
        cols={r[1] for r in self.conn.execute("PRAGMA table_info(missions)")}
        if "lease_token" not in cols:self.conn.execute("ALTER TABLE missions ADD COLUMN lease_token TEXT")
        if "max_attempts" not in cols:self.conn.execute(f"ALTER TABLE missions ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT {DEFAULT_MAX_ATTEMPTS}")
        self.conn.execute("INSERT INTO meta(key,value) VALUES('schema_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(SCHEMA_VERSION),))
    def _receipt_from_row(self,r):return {"event_id":r["event_id"],"mission_id":r["mission_id"],"type":r["type"],"payload":json.loads(r["payload_json"]),"created_ms":r["created_ms"],"prev_hash":r["prev_hash"],"event_hash":r["event_hash"]}
    def _insert_event_tx(self,event_type,payload,mission_id=None):
        prevrow=self.conn.execute("SELECT event_hash FROM events ORDER BY seq DESC LIMIT 1").fetchone();prev=prevrow[0] if prevrow else None;created=utc_ms();eid=str(uuid.uuid4());body={"event_id":eid,"mission_id":mission_id,"type":event_type,"payload":payload,"created_ms":created,"prev_hash":prev};h=sha256_bytes(canonical_json(body).encode());self.conn.execute("INSERT INTO events(event_id,mission_id,type,payload_json,created_ms,prev_hash,event_hash) VALUES(?,?,?,?,?,?,?)",(eid,mission_id,event_type,canonical_json(payload),created,prev,h));return {**body,"event_hash":h}
    def _receipt_bytes_from_db(self):return b"".join((canonical_json(self._receipt_from_row(r))+"\n").encode() for r in self.conn.execute("SELECT * FROM events ORDER BY seq"))
    def _sync_receipts(self):_atomic_bytes(self.paths.receipts,self._receipt_bytes_from_db())
    def append_event(self,event_type,payload,mission_id=None):
        self.conn.execute("BEGIN IMMEDIATE")
        try:r=self._insert_event_tx(event_type,payload,mission_id);self.conn.execute("COMMIT")
        except BaseException:
            if self.conn.in_transaction:self.conn.execute("ROLLBACK")
            raise
        self._sync_receipts();return r
    def add_mission(self,kind,spec,idempotency_key=None,max_attempts=None):
        sj=canonical_json(spec);sh=sha256_bytes(sj.encode());now=utc_ms();mid=str(uuid.uuid4());budget=max(1,int(max_attempts or DEFAULT_MAX_ATTEMPTS));self.conn.execute("BEGIN IMMEDIATE")
        try:
            if idempotency_key:
                old=self.conn.execute("SELECT id,kind,spec_sha256 FROM missions WHERE idempotency_key=?",(idempotency_key,)).fetchone()
                if old:
                    if old["kind"]!=kind or old["spec_sha256"]!=sh:raise RuntimeError("idempotency conflict: key already names different work")
                    self.conn.execute("COMMIT");return str(old["id"])
            self.conn.execute("INSERT INTO missions(id,kind,spec_json,spec_sha256,state,created_ms,updated_ms,max_attempts,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?)",(mid,kind,sj,sh,"queued",now,now,budget,idempotency_key));self._insert_event_tx("mission.queued",{"kind":kind,"spec_sha256":sh,"max_attempts":budget},mid);self.conn.execute("COMMIT")
        except BaseException:
            if self.conn.in_transaction:self.conn.execute("ROLLBACK")
            raise
        self._sync_receipts();return mid
    def lease_next(self,owner,ttl_ms=120000):
        while True:
            now=utc_ms();until=now+ttl_ms;token=str(uuid.uuid4());self.conn.execute("BEGIN IMMEDIATE")
            try:
                r=self.conn.execute("SELECT id,attempt,max_attempts FROM missions WHERE state='queued' OR (state='working' AND lease_until_ms IS NOT NULL AND lease_until_ms < ?) ORDER BY created_ms LIMIT 1",(now,)).fetchone()
                if not r:self.conn.execute("COMMIT");return None
                mid=r["id"]
                if r["attempt"]>=r["max_attempts"]:
                    self.conn.execute("UPDATE missions SET state='blocked',updated_ms=?,last_error='attempt budget exhausted',lease_owner=NULL,lease_until_ms=NULL,lease_token=NULL WHERE id=?",(now,mid));self._insert_event_tx("mission.blocked",{"reason":"attempt_budget_exhausted","attempt":r["attempt"],"max_attempts":r["max_attempts"]},mid);self.conn.execute("COMMIT");self._sync_receipts();continue
                cur=self.conn.execute("UPDATE missions SET state='working',lease_owner=?,lease_until_ms=?,lease_token=?,updated_ms=?,attempt=attempt+1 WHERE id=? AND (state='queued' OR (state='working' AND lease_until_ms < ?))",(owner,until,token,now,mid,now))
                if cur.rowcount!=1:self.conn.execute("ROLLBACK");continue
                self._insert_event_tx("mission.leased",{"owner":owner,"lease_until_ms":until,"lease_token":token},mid);self.conn.execute("COMMIT")
            except BaseException:
                if self.conn.in_transaction:self.conn.execute("ROLLBACK")
                raise
            self._sync_receipts();return self.get_mission(mid)
    def assert_lease(self,mid,token):
        r=self.get_mission(mid);now=utc_ms()
        if not r or r["state"]!="working" or r["lease_token"]!=token or not r["lease_until_ms"] or r["lease_until_ms"]<now:raise RuntimeError("lease lost or expired")
    def _assert_lease_tx(self,mid,token):
        r=self.conn.execute("SELECT * FROM missions WHERE id=?",(mid,)).fetchone();now=utc_ms()
        if not r or r["state"]!="working" or r["lease_token"]!=token or not r["lease_until_ms"] or r["lease_until_ms"]<now:raise RuntimeError("lease lost or expired")
        return r
    def transition(self,mid,new_state,*,lease_token=None,error=None,payload=None):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            r=self.conn.execute("SELECT * FROM missions WHERE id=?",(mid,)).fetchone()
            if not r:raise KeyError(mid)
            if r["state"] in TERMINAL_STATES:raise RuntimeError("terminal mission cannot transition")
            if r["state"]=="working":r=self._assert_lease_tx(mid,lease_token)
            old=r["state"];self.conn.execute("UPDATE missions SET state=?,updated_ms=?,last_error=?,lease_owner=NULL,lease_until_ms=NULL,lease_token=NULL WHERE id=?",(new_state,utc_ms(),error,mid));self._insert_event_tx(f"mission.{new_state}",{"from":old,"error":error,**(payload or {})},mid);self.conn.execute("COMMIT")
        except BaseException:
            if self.conn.in_transaction:self.conn.execute("ROLLBACK")
            raise
        self._sync_receipts()
    def promote_artifact(self,mid,lease_token,result):
        p=self.paths.root/result["relative_path"]
        if not p.exists() or sha256_bytes(p.read_bytes())!=result["sha256"]:raise RuntimeError("artifact missing or hash changed before promotion")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            r=self._assert_lease_tx(mid,lease_token);when=utc_ms();self.conn.execute("INSERT OR IGNORE INTO artifacts(sha256,mission_id,logical_name,relative_path,byte_count,promoted_ms) VALUES(?,?,?,?,?,?)",(result["sha256"],mid,result["logical_name"],result["relative_path"],result["byte_count"],when));self.conn.execute("INSERT INTO mission_artifacts(mission_id,sha256,logical_name,relative_path,byte_count,promoted_ms) VALUES(?,?,?,?,?,?)",(mid,result["sha256"],result["logical_name"],result["relative_path"],result["byte_count"],when));self.conn.execute("UPDATE missions SET state='promoted',updated_ms=?,last_error=NULL,lease_owner=NULL,lease_until_ms=NULL,lease_token=NULL WHERE id=?",(when,mid));self._insert_event_tx("mission.promoted",{"from":r["state"],**result},mid);self.conn.execute("COMMIT")
        except BaseException:
            if self.conn.in_transaction:self.conn.execute("ROLLBACK")
            raise
        self._sync_receipts()
    def requeue_blocked(self,mid,*,new_max_attempts=None):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            r=self.conn.execute("SELECT * FROM missions WHERE id=?",(mid,)).fetchone()
            if not r or r["state"]!="blocked":raise RuntimeError("mission is not blocked")
            budget=max(r["max_attempts"]+1,int(new_max_attempts or 0));self.conn.execute("UPDATE missions SET state='queued',max_attempts=?,updated_ms=?,last_error=NULL WHERE id=?",(budget,utc_ms(),mid));self._insert_event_tx("mission.requeued",{"max_attempts":budget},mid);self.conn.execute("COMMIT")
        except BaseException:
            if self.conn.in_transaction:self.conn.execute("ROLLBACK")
            raise
        self._sync_receipts()
    def get_mission(self,mid):return self.conn.execute("SELECT * FROM missions WHERE id=?",(mid,)).fetchone()
    def list_missions(self):return list(self.conn.execute("SELECT * FROM missions ORDER BY created_ms DESC"))
    def recover_expired(self):
        now=utc_ms();rows=list(self.conn.execute("SELECT id FROM missions WHERE state='working' AND lease_until_ms < ?",(now,)));n=0
        for x in rows:
            mid=x["id"];self.conn.execute("BEGIN IMMEDIATE")
            try:
                r=self.conn.execute("SELECT * FROM missions WHERE id=?",(mid,)).fetchone()
                if not r or r["state"]!="working" or not r["lease_until_ms"] or r["lease_until_ms"]>=utc_ms():self.conn.execute("ROLLBACK");continue
                state="blocked" if r["attempt"]>=r["max_attempts"] else "queued";reason="attempt_budget_exhausted" if state=="blocked" else "expired_lease";self.conn.execute("UPDATE missions SET state=?,lease_owner=NULL,lease_until_ms=NULL,lease_token=NULL,updated_ms=?,last_error=? WHERE id=?",(state,utc_ms(),reason,mid));self._insert_event_tx(f"mission.{state if state=='blocked' else 'recovered'}",{"reason":reason,"attempt":r["attempt"],"max_attempts":r["max_attempts"]},mid);self.conn.execute("COMMIT");n+=1
            except BaseException:
                if self.conn.in_transaction:self.conn.execute("ROLLBACK")
                raise
        if n:self._sync_receipts()
        return n
    def verify_chain(self):
        prev=None
        for r in self.conn.execute("SELECT * FROM events ORDER BY seq"):
            body={"event_id":r["event_id"],"mission_id":r["mission_id"],"type":r["type"],"payload":json.loads(r["payload_json"]),"created_ms":r["created_ms"],"prev_hash":r["prev_hash"]};expected=sha256_bytes(canonical_json(body).encode())
            if r["prev_hash"]!=prev:return False,f"broken prev_hash at event seq {r['seq']}"
            if r["event_hash"]!=expected:return False,f"broken event_hash at event seq {r['seq']}"
            prev=r["event_hash"]
        return True,"ok"
    def verify_receipts(self):
        expected=self._receipt_bytes_from_db()
        if not self.paths.receipts.exists():return (not expected,"missing receipts.jsonl" if expected else "ok")
        return (True,"ok") if self.paths.receipts.read_bytes()==expected else (False,"receipt/db divergence")
    def rebuild_receipts(self):
        ok,why=self.verify_chain()
        if not ok:raise RuntimeError(f"refuse receipt rebuild: {why}")
        before=sha256_bytes(self.paths.receipts.read_bytes()) if self.paths.receipts.exists() else None;data=self._receipt_bytes_from_db();_atomic_bytes(self.paths.receipts,data);repair={"schema":"clarity.receipt-repair.v1","at_ms":utc_ms(),"before_sha256":before,"after_sha256":sha256_bytes(data),"source":"sqlite-verified-event-chain","event_count":self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]};fd=os.open(self.paths.repairs,os.O_APPEND|os.O_CREAT|os.O_WRONLY,0o600)
        try:os.write(fd,(canonical_json(repair)+"\n").encode());os.fsync(fd)
        finally:os.close(fd)
        return repair["event_count"]
    def create_snapshot(self,label=None):
        self.checkpoint();stamp=f"{utc_ms()}-{label or 'snapshot'}";target=self.paths.snapshots/stamp;target.mkdir(parents=True,exist_ok=False);dbcopy=target/"clarity.db";dst=sqlite3.connect(dbcopy)
        try:self.conn.backup(dst)
        finally:dst.close()
        receipts=self.paths.receipts.read_bytes() if self.paths.receipts.exists() else b"";_atomic_bytes(target/"receipts.jsonl",receipts);entries=[]
        for r in self.conn.execute("SELECT DISTINCT sha256,relative_path FROM mission_artifacts ORDER BY sha256"):
            src=self.paths.root/r["relative_path"]
            if not src.exists() or sha256_bytes(src.read_bytes())!=r["sha256"]:raise RuntimeError(f"cannot snapshot missing/corrupt artifact: {r['relative_path']}")
            rel=Path(r["relative_path"]).relative_to("artifacts");dstp=target/"artifacts"/rel;dstp.parent.mkdir(parents=True,exist_ok=True)
            try:os.link(src,dstp)
            except OSError:shutil.copy2(src,dstp)
            entries.append({"relative_path":str(rel),"sha256":r["sha256"],"byte_count":src.stat().st_size})
        manifest={"schema":"clarity.snapshot.v2","created_ms":utc_ms(),"db_sha256":sha256_bytes(dbcopy.read_bytes()),"receipts_sha256":sha256_bytes(receipts),"artifacts":entries};_atomic_bytes(target/"manifest.json",(canonical_json(manifest)+"\n").encode());return target
    @staticmethod
    def restore_snapshot(root,snapshot):
        root=Path(root);snap=Path(snapshot);m=json.loads((snap/"manifest.json").read_text());db=(snap/"clarity.db").read_bytes();receipts=(snap/"receipts.jsonl").read_bytes()
        if m.get("schema")!="clarity.snapshot.v2" or sha256_bytes(db)!=m["db_sha256"] or sha256_bytes(receipts)!=m["receipts_sha256"]:raise RuntimeError("snapshot manifest/hash mismatch")
        for e in m.get("artifacts",[]):
            src=snap/"artifacts"/e["relative_path"]
            if not src.exists() or sha256_bytes(src.read_bytes())!=e["sha256"]:raise RuntimeError(f"snapshot artifact mismatch: {e['relative_path']}")
        root.mkdir(parents=True,exist_ok=True)
        for stale in (root/"clarity.db-wal",root/"clarity.db-shm"):stale.unlink(missing_ok=True)
        _atomic_bytes(root/"clarity.db",db);_atomic_bytes(root/"receipts.jsonl",receipts)
        for e in m.get("artifacts",[]):_atomic_bytes(root/"artifacts"/e["relative_path"],(snap/"artifacts"/e["relative_path"]).read_bytes())
        _fsync_dir(root)
    def orphan_artifacts(self):
        refs={str((self.paths.root/r[0]).resolve()) for r in self.conn.execute("SELECT DISTINCT relative_path FROM mission_artifacts")};return [p for p in self.paths.artifacts.rglob("*") if p.is_file() and str(p.resolve()) not in refs]
    def gc_orphan_artifacts(self):
        xs=self.orphan_artifacts()
        for p in xs:p.unlink(missing_ok=True)
        for p in sorted(self.paths.artifacts.rglob("*"),reverse=True):
            if p.is_dir():
                try:p.rmdir()
                except OSError:pass
        return len(xs)
    def integrity_check(self):v=self.conn.execute("PRAGMA integrity_check").fetchone()[0];return v=="ok",v
    def checkpoint(self):self.conn.execute("PRAGMA wal_checkpoint(FULL)")
    def rows_as_dicts(self,rows:Iterable[sqlite3.Row]):return [dict(r) for r in rows]
