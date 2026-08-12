#!/usr/bin/env python3
import argparse,json,subprocess,sys
from pathlib import Path

def git(repo,*args):
 p=subprocess.run(["git","-C",str(repo),*args],text=True,capture_output=True)
 if p.returncode: raise RuntimeError(p.stderr.strip() or "git failed")
 return p.stdout.strip()

def fail(msg):
 print(json.dumps({"ok":False,"error":msg},indent=2));return 2

def load(path):
 try:return json.loads(Path(path).read_text(encoding="utf-8"))
 except Exception as e:raise RuntimeError(f"invalid receipt {path}: {e}")

def validate(r):
 required={"schema":"venice-code-review-v2","verdict":"PASS","directive":"STOP_ALLOWED","externalReviewGateSatisfied":True,"stopPermissionGranted":True}
 for k,v in required.items():
  if r.get(k)!=v: raise RuntimeError(f"receipt rejected: {k}={r.get(k)!r}, expected {v!r}")
 if r.get("continuationRequired") or r.get("haltRequired"):raise RuntimeError("receipt contradictory: continuation/halt asserted with STOP_ALLOWED")
 if not r.get("repo") or not r.get("base") or not r.get("head") or not r.get("diffSha256"):raise RuntimeError("receipt lacks review identity/provenance")
 findings=r.get("findings") or []
 if any(str(x).startswith("hard_gate:") for x in findings):raise RuntimeError("receipt contains hard gate finding")
 return r

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--repo",default=".");ap.add_argument("--candidate",default="HEAD");ap.add_argument("receipts",nargs="+");a=ap.parse_args()
 try:
  repo=Path(git(a.repo,"rev-parse","--show-toplevel"));candidate=git(repo,"rev-parse",a.candidate)
  if len(a.receipts)<3:return fail("at least three Venice approvals are required")
  rs=[validate(load(p)) for p in a.receipts]
  heads=[]
  for r in rs:
   try:h=git(repo,"rev-parse",str(r["head"]))
   except Exception:raise RuntimeError(f"review head cannot be resolved in candidate repo: {r['head']}")
   heads.append(h)
  if len(set(heads))!=len(heads):raise RuntimeError("replayed approval: review heads must be distinct")
  if heads[-1]!=candidate:raise RuntimeError(f"stale final approval: {heads[-1]} != candidate {candidate}")
  out={"ok":True,"candidate":candidate,"veniceApprovalCycles":len(rs),"reviewHeads":heads,"directive":"STOP_ALLOWED","firstPlayAllowed":True}
  print(json.dumps(out,indent=2,sort_keys=True));return 0
 except Exception as e:return fail(str(e))
if __name__=="__main__":sys.exit(main())
