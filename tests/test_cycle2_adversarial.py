from pathlib import Path
import json,time
import pytest
from clarity.storage import Store
from clarity.executor import execute_local

def spec(v=1): return {"logical_name":"x.json","content":{"schema":"x.v1","value":v}}

def test_idempotency_key_refuses_different_work(tmp_path:Path):
 s=Store(tmp_path)
 try:
  s.add_mission("world.build_slice",spec(1),"k")
  with pytest.raises(RuntimeError,match="idempotency conflict"):s.add_mission("world.build_slice",spec(2),"k")
 finally:s.close()

def test_deleted_receipt_stream_is_detected(tmp_path:Path):
 s=Store(tmp_path)
 try:
  s.add_mission("world.build_slice",spec())
  s.paths.receipts.unlink();ok,why=s.verify_receipts();assert not ok;assert "missing" in why
 finally:s.close()

def test_modified_receipt_is_detected(tmp_path:Path):
 s=Store(tmp_path)
 try:
  s.add_mission("world.build_slice",spec());lines=s.paths.receipts.read_text().splitlines();r=json.loads(lines[0]);r["payload"]={"lies":True};s.paths.receipts.write_text(json.dumps(r)+"\n")
  assert s.verify_receipts()[0] is False
 finally:s.close()

def test_stale_worker_cannot_promote(tmp_path:Path):
 s=Store(tmp_path)
 try:
  mid=s.add_mission("world.build_slice",spec());old=dict(s.lease_next("old",ttl_ms=1));time.sleep(.01);assert s.recover_expired()==1;new=dict(s.lease_next("new",ttl_ms=10000));assert new["lease_token"]!=old["lease_token"]
  with pytest.raises(RuntimeError,match="lease lost"):execute_local(s,old)
  assert s.get_mission(mid)["state"]=="working"
 finally:s.close()
