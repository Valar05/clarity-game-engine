from pathlib import Path
import json,time
import pytest
from clarity.storage import Store,sha256_bytes
from clarity.executor import execute_local

def spec(v=1):return {"logical_name":"x.json","content":{"schema":"x.v1","value":v}}

def test_attempt_budget_blocks_poison_job(tmp_path:Path):
 s=Store(tmp_path)
 try:
  mid=s.add_mission("world.build_slice",spec(),max_attempts=1);m=s.lease_next("dead",ttl_ms=1);assert m["attempt"]==1;time.sleep(.01);s.recover_expired();assert s.get_mission(mid)["state"]=="blocked"
  assert s.lease_next("other") is None
  s.requeue_blocked(mid,new_max_attempts=2);assert s.get_mission(mid)["state"]=="queued"
 finally:s.close()

def test_receipts_can_be_rebuilt_only_from_valid_db_chain(tmp_path:Path):
 s=Store(tmp_path)
 try:
  s.add_mission("world.build_slice",spec());s.paths.receipts.unlink();assert not s.verify_receipts()[0];assert s.rebuild_receipts()>=1;assert s.verify_receipts()==(True,"ok")
  with s.conn:s.conn.execute("UPDATE events SET payload_json='{}' WHERE seq=1")
  with pytest.raises(RuntimeError,match="refuse receipt rebuild"):s.rebuild_receipts()
 finally:s.close()

def test_snapshot_restore_rehearsal(tmp_path:Path):
 root=tmp_path/"live";s=Store(root)
 try:
  mid=s.add_mission("world.build_slice",spec());snap=s.create_snapshot("before-damage")
 finally:s.close()
 damaged=Store(root)
 try:
  with damaged.conn:damaged.conn.execute("DELETE FROM missions WHERE id=?",(mid,))
 finally:damaged.close()
 Store.restore_snapshot(root,snap);restored=Store(root)
 try:
  assert restored.get_mission(mid) is not None;assert restored.integrity_check()==(True,"ok");assert restored.verify_chain()==(True,"ok");assert restored.verify_receipts()==(True,"ok")
 finally:restored.close()

def test_orphan_blob_is_reported_and_collected(tmp_path:Path):
 s=Store(tmp_path)
 try:
  orphan=s.paths.artifacts/"ff"/"dead"/"orphan.bin";orphan.parent.mkdir(parents=True);orphan.write_bytes(b"orphan");assert orphan in s.orphan_artifacts();assert s.gc_orphan_artifacts()==1;assert not orphan.exists()
 finally:s.close()

def test_successful_blob_is_not_collected(tmp_path:Path):
 s=Store(tmp_path)
 try:
  mid=s.add_mission("world.build_slice",spec());m=dict(s.lease_next("worker",ttl_ms=10000));result=execute_local(s,m);s.transition(mid,"promoted",lease_token=m["lease_token"],payload=result);assert s.gc_orphan_artifacts()==0;assert (s.paths.root/result["relative_path"]).exists()
 finally:s.close()
