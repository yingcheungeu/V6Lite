"""Direct PowerShell entry and real Ctrl+C on owned TEMP consoles only."""
from __future__ import annotations
import json, os, shutil, subprocess, sys, time
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[2]
ENTRY=ROOT/"scripts/v6-user.ps1"
PS=shutil.which("powershell.exe")
pytestmark=pytest.mark.skipif(os.name!="nt" or not PS,reason="Windows PowerShell required")
def literal(path): return "'"+str(path).replace("'","''")+"'"
def test_direct_entry_is_one_line_and_has_no_cmd_hop():
    assert ENTRY.read_text(encoding="utf-8").strip()=="& 'C:\\Dev\\V6Lite\\scripts\\v6.ps1' @args"
@pytest.mark.parametrize("args",[("dry",),("g3t-dry",),("g3t",),("g3n-dry",),("g3n",),("entry","AAPL"),("status","g3t"),("g3t","-WhatIf"),("status","g3t","-WhatIf")])
def test_direct_entry_forwards_exact_arguments_and_returns_to_parent(tmp_path,args):
    endpoint=tmp_path/"mock endpoint.ps1";result=tmp_path/"args.json";returned=tmp_path/"parent_returned.txt"
    endpoint.write_text("[IO.File]::WriteAllText("+literal(result)+", (ConvertTo-Json -InputObject @($args) -Compress))\nexit 7\n",encoding="utf-8")
    copied=tmp_path/"user.ps1";copied.write_text(ENTRY.read_text(encoding="utf-8").replace("C:\\Dev\\V6Lite\\scripts\\v6.ps1",str(endpoint)),encoding="utf-8")
    wrapper=tmp_path/"parent.ps1";wrapper.write_text("& "+literal(copied)+" "+" ".join(literal(a) for a in args)+"\n[IO.File]::WriteAllText("+literal(returned)+", [string]$LASTEXITCODE)\n",encoding="utf-8")
    completed=subprocess.run([PS,"-NoLogo","-NoProfile","-ExecutionPolicy","Bypass","-File",str(wrapper)],capture_output=True,text=True,timeout=20)
    assert completed.returncode==0,(completed.stdout,completed.stderr)
    assert json.loads(result.read_text(encoding="utf-8"))==list(args)
    assert returned.read_text()=="7"

@pytest.mark.parametrize('values', [
    ['', 'two words', 'plain'], ['embedded"quote', 'backslash'+chr(92)+'"quote'],
    ['trailing path '+chr(92), 'double '+chr(92)*2, 'space '+chr(92)+'" end'],
    ['A;B', '$(never-evaluate)', '&', 'unicode-中文']])
def test_native_console_argv_roundtrip_without_shell(tmp_path,values):
    from scripts.tests.test_v6_launchers import clone_temp_venv
    root=tmp_path/'native args';root.mkdir();clone_temp_venv(root)
    child=tmp_path/'argv_child.py';child.write_text('import json,sys;print("ARGV="+json.dumps(sys.argv[1:]));sys.exit(7)\n',encoding='utf-8')
    harness=tmp_path/'argv.ps1'
    harness.write_text('. '+literal(ROOT/'scripts/v6.ps1')+'\n$script:V6RepositoryRoot='+literal(root)+'\n$code=Invoke-V6Python -Arguments @('+','.join(literal(a) for a in ['-B',str(child),*values])+')\nWrite-Host ("CODE=" + $code)\n',encoding='utf-8-sig')
    env=os.environ.copy();env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1')
    completed=subprocess.run([PS,'-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-File',str(harness)],env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20)
    assert completed.returncode==0,(completed.stdout,completed.stderr)
    line=next(line[5:] for line in completed.stdout.splitlines() if line.startswith('ARGV='))
    assert json.loads(line)==values
    assert 'CODE=7' in completed.stdout

@pytest.mark.parametrize('shell', [shutil.which('powershell.exe'),shutil.which('pwsh.exe')])
@pytest.mark.parametrize('mode', ['dry','paper_fake'])
def test_one_ctrl_c_waits_for_complete_synthetic_shutdown(tmp_path,shell,mode):
    if not shell:pytest.skip('requested PowerShell host unavailable')
    from scripts.tests.console_shutdown_probe import run_probe
    result=run_probe(shell,mode,tmp_path/'owned console')
    assert result['result']=='PASS' and result['python_exited_before_prompt']
