from __future__ import annotations

import json,time
from pathlib import Path
from clarity.executor import execute_local
from clarity.storage import Store,sha256_bytes

def spec():return {"logical_name":"april-test-node.json","content":{"schema":"clarity.world-node.v1","id":"april-test-node","offline":True,"requires_model":False}}
def promote(s):
 mid=s.add_mission("world.build_slice",spec());m=dict(s.lease_next("worker",ttl_ms=10000));result=execute_local(s,m);s.promote_artifact(mid,m["lease_token"],result);return mid,result

def test_idempotent_enqueue(tmp_path:Path):
 s=Store(tmp_path)
 try:a=s.add_mission("world.build_slice",spec(),"same-key");b=s.add_mission("world.build_slice",spec(),"same-key");assert a==b;assert len(s.list_missions())==1
 finally:s.close()
def test_expired_lease_recovers(tmp_path:Path):
 s=Store(tmp_path)
 try:mid=s.add_mission("world.build_slice",spec());leased=s.lease_next("dead-worker",ttl_ms=1);assert leased["id"]==mid;time.sleep(.01);assert s.recover_expired()==1;assert s.get_mission(mid)["state"]=="queued"
 finally:s.close()
def test_artifact_promotes_only_after_readback(tmp_path:Path):
 s=Store(tmp_path)
 try:
  mid,result=promote(s);artifact=s.paths.root/result["relative_path"];assert artifact.exists();assert sha256_bytes(artifact.read_bytes())==result["sha256"];assert s.get_mission(mid)["state"]=="promoted";assert s.conn.execute("SELECT COUNT(*) FROM mission_artifacts WHERE mission_id=?",(mid,)).fetchone()[0]==1
 finally:s.close()
def test_identical_blob_keeps_both_mission_lineages(tmp_path:Path):
 s=Store(tmp_path)
 try:
  a,ra=promote(s);b,rb=promote(s);assert ra["sha256"]==rb["sha256"];rows=list(s.conn.execute("SELECT mission_id FROM mission_artifacts WHERE sha256=? ORDER BY mission_id",(ra["sha256"],)));assert {r[0] for r in rows}=={a,b};assert s.conn.execute("SELECT COUNT(*) FROM artifacts WHERE sha256=?",(ra["sha256"],)).fetchone()[0]==1
 finally:s.close()
def test_hash_chain_detects_db_tamper(tmp_path:Path):
 s=Store(tmp_path)
 try:
  mid=s.add_mission("world.build_slice",spec());assert s.verify_chain()[0]
  with s.conn:s.conn.execute("UPDATE events SET payload_json='{}' WHERE mission_id=?",(mid,))
  ok,reason=s.verify_chain();assert not ok;assert "event_hash" in reason
 finally:s.close()
def test_receipt_stream_exists_and_is_parseable(tmp_path:Path):
 s=Store(tmp_path)
 try:
  s.add_mission("world.build_slice",spec());lines=s.paths.receipts.read_text("utf-8").splitlines();assert lines
  for line in lines:obj=json.loads(line);assert obj["event_hash"];assert obj["type"]
 finally:s.close()
def test_sqlite_integrity_chain_and_receipts_survive_reopen(tmp_path:Path):
 s=Store(tmp_path);mid=s.add_mission("world.build_slice",spec());s.checkpoint();s.close();r=Store(tmp_path)
 try:assert r.get_mission(mid) is not None;assert r.integrity_check()==(True,"ok");assert r.verify_chain()==(True,"ok");assert r.verify_receipts()==(True,"ok")
 finally:r.close()
