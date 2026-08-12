#!/usr/bin/env python3
import argparse,json,subprocess,sys
from pathlib import Path

def git(repo,*args):
 p=subprocess.run(["git","-C",str(repo),*args],text=True,capture_output=True)
 if p.returncode:raise RuntimeError(p.stderr.strip() or "git failed")
 return p.stdout.strip()
def fail(msg):print(json.dumps({"ok":False,"error":msg},indent=2));return 2
def load(path):
 try:return json.loads(Path(path).read_text(encoding="utf-8"))
 except Exception as e:raise RuntimeError(f"invalid receipt {path}: {e}")
def validate(r):
 required={"schema":"venice-code-review-v2","verdict":"PASS","directive":"STOP_ALLOWED","externalReviewGateSatisfied":True,"stopPermissionGranted":True}
 for k,v in required.items():
  if r.get(k)!=v:raise RuntimeError(f"receipt rejected: {k}={r.get(k)!r}, expected {v!r}")
 if r.get("continuationRequired") or r.get("haltRequired"):raise RuntimeError("receipt contradictory: continuation/halt asserted with STOP_ALLOWED")
 for k in ("repo","baseCommit","headCommit","fullDiffSha256"):
  if not r.get(k):raise RuntimeError(f"receipt lacks immutable provenance field: {k}")
 if len(str(r["fullDiffSha256"]))!=64:raise RuntimeError("invalid fullDiffSha256")
 findings=r.get("findings") or []
 if any(str(x).startswith("hard_gate:") for x in findings):raise RuntimeError("receipt contains hard gate finding")
 return r
def main():
 ap=argparse.ArgumentParser();ap.add_argument("--repo",default=".");ap.add_argument("--candidate",default="HEAD");ap.add_argument("receipts",nargs="+");a=ap.parse_args()
 try:
  repo=Path(git(a.repo,"rev-parse","--show-toplevel"));candidate=git(repo,"rev-parse",f"{a.candidate}^{{commit}}")
  if len(a.receipts)<3:return fail("at least three Venice approvals are required")
  rs=[validate(load(p)) for p in a.receipts];heads=[]
  for r in rs:
   h=str(r["headCommit"])
   if git(repo,"cat-file","-t",h)!="commit":raise RuntimeError(f"reviewed commit unavailable: {h}")
   if git(repo,"rev-parse",f"{h}^{{commit}}")!=h:raise RuntimeError(f"noncanonical reviewed commit: {h}")
   heads.append(h)
  if len(set(heads))!=len(heads):raise RuntimeError("replayed approval: review heads must be distinct")
  for earlier,later in zip(heads,heads[1:]):
   p=subprocess.run(["git","-C",str(repo),"merge-base","--is-ancestor",earlier,later])
   if p.returncode!=0:raise RuntimeError("approval cycles are not an ordered ancestry chain")
  if heads[-1]!=candidate:raise RuntimeError(f"stale final approval: {heads[-1]} != candidate {candidate}")
  print(json.dumps({"ok":True,"candidate":candidate,"veniceApprovalCycles":len(rs),"reviewHeads":heads,"directive":"STOP_ALLOWED","firstPlayAllowed":True},indent=2,sort_keys=True));return 0
 except Exception as e:return fail(str(e))
if __name__=="__main__":sys.exit(main())
