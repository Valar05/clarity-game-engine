from __future__ import annotations
import argparse,json,os,socket,sys
from pathlib import Path
from .executor import VerificationError,execute_local
from .storage import Store,paths,sha256_bytes

def _json(v): print(json.dumps(v,indent=2,sort_keys=True,default=str))
def cmd_init(a):
 s=Store(a.root)
 try:s.append_event("system.initialized",{"schema_version":2,"host":socket.gethostname()});s.checkpoint();_json({"ok":True,"root":str(s.paths.root)});return 0
 finally:s.close()
def _health(s):
 iok,i=s.integrity_check(); cok,c=s.verify_chain(); rok,r=s.verify_receipts(); return iok and cok and rok,{"sqlite_integrity":i,"event_chain":c,"receipt_chain":r}
def cmd_status(a):
 s=Store(a.root)
 try:ok,h=_health(s);_json({"ok":ok,**h,"missions":s.rows_as_dicts(s.list_missions())});return 0 if ok else 2
 finally:s.close()
def cmd_add(a):
 s=Store(a.root)
 try:
  spec=json.loads(Path(a.spec).read_text("utf-8"));mid=s.add_mission(a.kind,spec,a.idempotency_key);_json({"ok":True,"mission_id":mid});return 0
 finally:s.close()
def cmd_list(a):
 s=Store(a.root)
 try:_json(s.rows_as_dicts(s.list_missions()));return 0
 finally:s.close()
def cmd_run(a):
 s=Store(a.root);owner=a.owner or f"{socket.gethostname()}:{os.getpid()}"
 try:
  s.recover_expired();row=s.lease_next(owner,a.lease_ms)
  if not row:_json({"ok":True,"worked":False,"reason":"queue_empty"});return 0
  m=dict(row);token=m["lease_token"]
  try:
   result=execute_local(s,m);s.transition(m["id"],"promoted",lease_token=token,payload=result);_json({"ok":True,"worked":True,"mission_id":m["id"],"result":result});return 0
  except VerificationError as e:s.transition(m["id"],"rejected",lease_token=token,error=str(e));_json({"ok":False,"error":str(e)});return 3
  except BaseException as e:s.append_event("mission.worker_crash",{"owner":owner,"error":repr(e)},m["id"]);raise
 finally:s.close()
def cmd_recover(a):
 s=Store(a.root)
 try:n=s.recover_expired();ok,h=_health(s);s.checkpoint();_json({"ok":ok,"recovered":n,**h});return 0 if ok else 2
 finally:s.close()
def cmd_verify(a):
 s=Store(a.root)
 try:
  ok,h=_health(s);missing=[];bad=[]
  for r in s.conn.execute("SELECT * FROM artifacts"):
   p=s.paths.root/r["relative_path"]
   if not p.exists():missing.append(r["relative_path"])
   elif sha256_bytes(p.read_bytes())!=r["sha256"]:bad.append(r["relative_path"])
  ok=ok and not missing and not bad;_json({"ok":ok,**h,"missing_artifacts":missing,"bad_artifact_hashes":bad});return 0 if ok else 2
 finally:s.close()
def cmd_doctor(a):
 p=paths(a.root);checks={"python":sys.version.split()[0],"root_parent_exists":p.root.parent.exists(),"root_parent_writable":os.access(p.root.parent,os.W_OK),"platform":sys.platform,"termux":"com.termux" in os.environ.get("PREFIX","")};ok=checks["root_parent_exists"] and checks["root_parent_writable"];_json({"ok":ok,"checks":checks});return 0 if ok else 2
def build_parser():
 p=argparse.ArgumentParser(prog="clarity");p.add_argument("--root");sub=p.add_subparsers(dest="command",required=True)
 for name,fn in [("init",cmd_init),("status",cmd_status),("doctor",cmd_doctor),("recover",cmd_recover),("verify",cmd_verify)]:x=sub.add_parser(name);x.set_defaults(func=fn)
 mission=sub.add_parser("mission");ms=mission.add_subparsers(dest="mission_command",required=True)
 x=ms.add_parser("add");x.add_argument("--kind",required=True);x.add_argument("--spec",required=True);x.add_argument("--idempotency-key");x.set_defaults(func=cmd_add)
 x=ms.add_parser("list");x.set_defaults(func=cmd_list)
 x=ms.add_parser("run");x.add_argument("--once",action="store_true");x.add_argument("--owner");x.add_argument("--lease-ms",type=int,default=120000);x.set_defaults(func=cmd_run)
 return p
def main(argv=None):a=build_parser().parse_args(argv);return int(a.func(a))
if __name__=="__main__":raise SystemExit(main())
