from __future__ import annotations
import json,os,tempfile
from pathlib import Path
from typing import Any
from .storage import Store,canonical_json,sha256_bytes
class VerificationError(RuntimeError):pass
def _atomic_write(path:Path,data:bytes):
 path.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(prefix=f".{path.name}.",dir=path.parent)
 try:
  with os.fdopen(fd,"wb") as f:f.write(data);f.flush();os.fsync(f.fileno())
  os.replace(tmp,path);d=os.open(path.parent,os.O_RDONLY)
  try:os.fsync(d)
  finally:os.close(d)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
def execute_local(store:Store,mission:dict[str,Any]):
 kind=mission["kind"];spec=json.loads(mission["spec_json"]);mid=mission["id"];token=mission.get("lease_token");store.assert_lease(mid,token)
 if kind!="world.build_slice":raise VerificationError(f"unsupported local mission kind: {kind}")
 name=str(spec.get("logical_name","")).strip();content=spec.get("content");expected=spec.get("expected_sha256")
 if not name or not isinstance(content,dict):raise VerificationError("world.build_slice requires logical_name and object content")
 data=(canonical_json(content)+"\n").encode();digest=sha256_bytes(data)
 if expected and digest!=expected:raise VerificationError(f"content hash mismatch: expected {expected}, got {digest}")
 q=store.paths.quarantine/mid/token/name;_atomic_write(q,data);rb=q.read_bytes()
 if rb!=data or sha256_bytes(rb)!=digest:raise VerificationError("quarantine readback mismatch")
 store.assert_lease(mid,token)
 blob=store.paths.artifacts/digest[:2]/digest/name;_atomic_write(blob,rb)
 if sha256_bytes(blob.read_bytes())!=digest:raise VerificationError("artifact blob readback mismatch")
 # The blob store is immutable/cache-like. No canonical DB mutation happens here.
 # Store.promote_artifact() atomically claims the blob, mission state, and event ledger.
 store.assert_lease(mid,token);rel=str(blob.relative_to(store.paths.root))
 return {"sha256":digest,"logical_name":name,"relative_path":rel,"byte_count":len(data)}
