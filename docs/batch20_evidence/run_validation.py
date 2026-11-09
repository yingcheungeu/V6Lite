"""Run immutable B19 baseline then three serial frozen Windows suites."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import ast, hashlib, json, os, platform, subprocess, sys, tempfile, zipfile
sys.path.insert(0, str(Path.cwd()))
from v6lite.version import code_sha
ROOT=Path.cwd(); E=ROOT/"docs/batch20_evidence"; PT=ZoneInfo("America/Los_Angeles")
BASE="abbdb4f2f7a558262a0859d5a6bf7ee859e46d2f"
def now(): return datetime.now(PT).isoformat()
def save(name,data): (E/name).write_text(json.dumps(data,indent=2,ensure_ascii=False)+"\n",encoding="utf-8",newline="\n")
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def method(text):
    tree=ast.parse(text)
    return ast.dump(next(n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name=="_apply_releases"),include_attributes=False)
head=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()
branch=subprocess.check_output(["git","branch","--show-current"],text=True).strip()
assert head==BASE and branch=="dev"
assert subprocess.run(["git","diff","--cached","--quiet"]).returncode==0
sha=code_sha(); manual=digest(ROOT/"docs/V6_LITE_OPERATOR_MANUAL.md")
assert manual=="5d7eb6f043f4d933019f36b6cbfd28b28f8474c0b7097261f9bb77ae627133bf"
original=subprocess.check_output(["git","show",BASE+":v6lite/engine.py"],text=True,encoding="utf-8")
assert method(original)==method((ROOT/"v6lite/engine.py").read_text(encoding="utf-8"))
freeze=json.loads((E/"freeze_environment.json").read_text(encoding="utf-8"))
freeze.update(code_sha=sha,freeze_pt=now(),release_method_AST_unchanged=True,python=sys.version,platform=platform.platform())
save("freeze_environment.json",freeze)
env=os.environ.copy(); env.update(PYTHONDONTWRITEBYTECODE="1",PYTHONUTF8="1",PYTHONUNBUFFERED="1",V6LITE_LEGACY_SCANNER=r"C:\Dev\TradingV6\market_scanner.py")
env.pop("V6LITE_TEST_PROTECTION",None)
base=Path(tempfile.mkdtemp(prefix="v6lite_B20_delivery_")); archive=base/"baseline.zip"; source=base/"source"
subprocess.run(["git","archive","--format=zip",f"--output={archive}",BASE],check=True)
with zipfile.ZipFile(archive) as z: z.extractall(source)
assert code_sha(source/"v6lite")=="6293dd4e2377603c"
files=sorted((ROOT/"v6lite/tests").glob("test_batch20_*.py")); assert len(files)==5
for p in files: (source/"v6lite/tests"/p.name).write_bytes(p.read_bytes())
start=now()
with (E/"baseline_delivery.log").open("w",encoding="utf-8") as log:
    result=subprocess.run([sys.executable,"-u","-B","-m","pytest",*["v6lite/tests/"+p.name for p in files],"-q","-p","no:cacheprovider","--tb=short","--junitxml",str(E/"baseline_delivery.xml")],cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
assert result.returncode==1
save("baseline_delivery_metadata.json",dict(baseline_commit=BASE,baseline_package_code_sha="6293dd4e2377603c",production_source="fresh git archive; only five final new tests copied",candidate_code_sha=sha,temp_source=str(source),start_pt=start,end_pt=now(),exit_code=result.returncode,test_files={p.name:digest(p) for p in files}))
print("FINAL_BASELINE "+(E/"baseline_delivery.log").read_text(encoding="utf-8").splitlines()[-1],flush=True)
runs=[];save("windows_full_results.json",runs)
for i in range(1,4):
    current=datetime.now(PT)
    assert not (current.weekday()<5 and 6*60 <= current.hour*60+current.minute < 13*60+30)
    assert code_sha()==sha and digest(ROOT/"docs/V6_LITE_OPERATOR_MANUAL.md")==manual
    start=now(); print(f"START_FULL_{i} {start} {sha}",flush=True)
    with (E/f"windows_full_{i}.log").open("w",encoding="utf-8") as log:
        result=subprocess.run([sys.executable,"-u","-B","-m","pytest","v6lite/tests","scripts/tests","-q","-p","no:cacheprovider","--tb=short","--junitxml",str(E/f"windows_full_{i}.xml")],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
    tail=(E/f"windows_full_{i}.log").read_text(encoding="utf-8").splitlines()[-1]
    item=dict(run=i,start_pt=start,end_pt=now(),exit_code=result.returncode,code_sha_before=sha,code_sha_after=code_sha(),tail=tail)
    runs.append(item);save("windows_full_results.json",runs);print("END_FULL "+json.dumps(item,ensure_ascii=False),flush=True)
    assert result.returncode==0 and code_sha()==sha
print("THREE_SERIAL_FROZEN_RUNS_COMPLETE",flush=True)
