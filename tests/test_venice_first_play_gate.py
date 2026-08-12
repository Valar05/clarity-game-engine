from __future__ import annotations
import json,subprocess,sys,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
GATE=ROOT/"scripts"/"venice-first-play-gate.py"

def git(repo,*args):return subprocess.run(["git","-C",str(repo),*args],check=True,text=True,capture_output=True).stdout.strip()
def write_receipt(path,repo,head,**overrides):
 r={"schema":"venice-code-review-v2","verdict":"PASS","directive":"STOP_ALLOWED","externalReviewGateSatisfied":True,"stopPermissionGranted":True,"continuationRequired":False,"haltRequired":False,"repo":str(repo),"base":"HEAD~1","head":head,"diffSha256":"a"*64,"findings":[]};r.update(overrides);path.write_text(json.dumps(r)+"\n")
def run_gate(repo,receipts,candidate="HEAD"):
 return subprocess.run([sys.executable,str(GATE),"--repo",str(repo),"--candidate",candidate,*map(str,receipts)],text=True,capture_output=True)
def make_repo():
 td=tempfile.TemporaryDirectory();repo=Path(td.name);git(repo,"init","-q");git(repo,"config","user.email","x@example.invalid");git(repo,"config","user.name","x")
 heads=[]
 for i in range(3):
  (repo/"x.txt").write_text(str(i));git(repo,"add",".");git(repo,"commit","-qm",f"c{i}");heads.append(git(repo,"rev-parse","HEAD"))
 return td,repo,heads

def test_requires_three_distinct_current_approvals():
 td,repo,heads=make_repo()
 try:
  receipts=[]
  for i,h in enumerate(heads):p=repo/f"r{i}.json";write_receipt(p,repo,h);receipts.append(p)
  p=run_gate(repo,receipts);assert p.returncode==0,p.stdout+p.stderr;assert json.loads(p.stdout)["firstPlayAllowed"] is True
  p=run_gate(repo,receipts[:2]);assert p.returncode!=0
  dup=repo/"dup.json";write_receipt(dup,repo,heads[1]);p=run_gate(repo,[receipts[0],receipts[1],dup]);assert p.returncode!=0
  bad=repo/"bad.json";write_receipt(bad,repo,heads[2],directive="CONTINUE_REQUIRED",verdict="FAIL",stopPermissionGranted=False,continuationRequired=True,externalReviewGateSatisfied=False);p=run_gate(repo,[receipts[0],receipts[1],bad]);assert p.returncode!=0
 finally:td.cleanup()
