"""Real CTRL_C_EVENT confined to an owned hidden TEMP console."""
from __future__ import annotations
import ctypes, json, os, shutil, subprocess, sys, tempfile, time
from ctypes import wintypes as w
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
CHILD=ROOT/'scripts/tests/console_shutdown_child.py'
def lit(p):return "'"+str(p).replace("'","''")+"'"
def load(p):
    try:return json.loads(p.read_text(encoding='utf-8'))
    except (FileNotFoundError,json.JSONDecodeError):return None
HELPER=r"""
import ctypes,json,os,sys,time
from ctypes import wintypes as w
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.AttachConsole.argtypes=[w.DWORD];k.AttachConsole.restype=w.BOOL
k.SetConsoleCtrlHandler.argtypes=[ctypes.c_void_p,w.BOOL];k.SetConsoleCtrlHandler.restype=w.BOOL
k.GetConsoleProcessList.argtypes=[ctypes.POINTER(w.DWORD),w.DWORD];k.GetConsoleProcessList.restype=w.DWORD
k.GenerateConsoleCtrlEvent.argtypes=[w.DWORD,w.DWORD];k.GenerateConsoleCtrlEvent.restype=w.BOOL
k.CreateToolhelp32Snapshot.argtypes=[w.DWORD,w.DWORD];k.CreateToolhelp32Snapshot.restype=w.HANDLE
k.CloseHandle.argtypes=[w.HANDLE];k.CloseHandle.restype=w.BOOL
class PE(ctypes.Structure):
    _fields_=[('dwSize',w.DWORD),('cntUsage',w.DWORD),('th32ProcessID',w.DWORD),('th32DefaultHeapID',ctypes.c_void_p),('th32ModuleID',w.DWORD),('cntThreads',w.DWORD),('th32ParentProcessID',w.DWORD),('pcPriClassBase',w.LONG),('dwFlags',w.DWORD),('szExeFile',w.WCHAR*260)]
k.Process32FirstW.argtypes=[w.HANDLE,ctypes.POINTER(PE)];k.Process32FirstW.restype=w.BOOL
k.Process32NextW.argtypes=[w.HANDLE,ctypes.POINTER(PE)];k.Process32NextW.restype=w.BOOL
pid=int(sys.argv[1]);out=sys.argv[2]
k.FreeConsole();assert k.AttachConsole(pid),ctypes.get_last_error()
assert k.SetConsoleCtrlHandler(None,True)
arr=(w.DWORD*64)();n=k.GetConsoleProcessList(arr,64);assert 0<n<=64
members=set(arr[:n]);snapshot=k.CreateToolhelp32Snapshot(2,0);parents={};row=PE();row.dwSize=ctypes.sizeof(row)
try:
    ok=k.Process32FirstW(snapshot,ctypes.byref(row))
    while ok:parents[row.th32ProcessID]=row.th32ParentProcessID;ok=k.Process32NextW(snapshot,ctypes.byref(row))
finally:k.CloseHandle(snapshot)
def belongs(p):
    seen=set()
    while p and p not in seen:
        if p==pid:return True
        seen.add(p);p=parents.get(p,0)
    return False
assert pid in members and all(p==os.getpid() or belongs(p) for p in members),sorted(members)
result={'attached_console_pids':sorted(members),'ownership_checked':True,'helper_pid':os.getpid(),'signal':'CTRL_C_EVENT','signal_count_sent':1,'sent_at':time.time()}
assert k.GenerateConsoleCtrlEvent(0,0),ctypes.get_last_error()
time.sleep(.2);k.FreeConsole()
open(out,'w',encoding='utf-8').write(json.dumps(result))
"""
def process_alive(pid):
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];k.OpenProcess.restype=w.HANDLE
    k.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD];k.WaitForSingleObject.restype=w.DWORD
    k.CloseHandle.argtypes=[w.HANDLE];k.CloseHandle.restype=w.BOOL
    h=k.OpenProcess(0x100000,False,pid)
    if not h:return False
    try:return k.WaitForSingleObject(h,0)==258
    finally:k.CloseHandle(h)
def run_probe(shell,mode,work):
    work=Path(work);work.mkdir(parents=True,exist_ok=True);temp_root=work/'temp runtime';temp_root.mkdir()
    python=temp_root/'.venv/Scripts/python.exe';python.parent.mkdir(parents=True);shutil.copy2(sys.executable,python)
    (temp_root/'.venv/pyvenv.cfg').write_text(f'home = {sys.base_prefix}\ninclude-system-site-packages = true\nversion = 3.11\n',encoding='utf-8')
    endpoint=work/'mock safe launcher.ps1'
    endpoint.write_text(". "+lit(ROOT/'scripts/v6.ps1')+"\n$script:V6RepositoryRoot="+lit(temp_root)+"\nexit (Invoke-V6Python -Arguments @('-u','-B',"+lit(CHILD)+","+lit(ROOT)+","+lit(work)+","+lit(mode)+"))\n",encoding='utf-8')
    entry=work/'user entry.ps1';entry.write_text((ROOT/'scripts/v6-user.ps1').read_text().replace(r'C:\Dev\V6Lite\scripts\v6.ps1',str(endpoint)),encoding='utf-8')
    caller=work/'caller.ps1';prompt=work/'prompt_events.jsonl'
    caller.write_text("function global:prompt {\n$ready="+lit(work/'ready.json')+"\nif (Test-Path -LiteralPath $ready) {\n$r=Get-Content -LiteralPath $ready -Raw | ConvertFrom-Json\n$alive=$null -ne (Get-Process -Id $r.pid -ErrorAction SilentlyContinue)\n[IO.File]::AppendAllText("+lit(prompt)+", ((@{at=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0;python_alive=$alive;parent_buffering_restored=([Environment]::GetEnvironmentVariable('PYTHONUNBUFFERED','Process') -ceq 'synthetic-parent-buffering');cwd_unchanged=((Get-Location).Path -ceq "+lit(work)+")}|ConvertTo-Json -Compress)+[Environment]::NewLine))\n}\nreturn 'OWNED-TEMP-PS>'\n}\n& "+lit(entry)+"\n",encoding='utf-8')
    helper=work/'signal_helper.py';helper.write_text(HELPER,encoding='utf-8')
    startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
    env=os.environ.copy();env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1',PYTHONUNBUFFERED='synthetic-parent-buffering');env.pop('V6LITE_TEST_PROTECTION',None)
    log=(work/'console.log').open('w',encoding='utf-8')
    ps=subprocess.Popen([shell,'-NoLogo','-NoProfile','-NoExit','-ExecutionPolicy','Bypass','-File',str(caller)],cwd=work,env=env,startupinfo=startup,creationflags=subprocess.CREATE_NEW_CONSOLE,stdout=log,stderr=subprocess.STDOUT)
    ready=None
    try:
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            ready=load(work/'ready.json')
            if ready:break
            if ps.poll() is not None:break
            time.sleep(.05)
        assert ready is not None,'child did not become ready: '+(work/'console.log').read_text(encoding='utf-8',errors='replace')[-2000:]
        assert ready['unbuffered_is_one']
        signal_file=work/'signal.json'
        sent=subprocess.run([sys.executable,'-B',str(helper),str(ps.pid),str(signal_file)],env=env,creationflags=subprocess.CREATE_NO_WINDOW,capture_output=True,text=True,timeout=10)
        assert sent.returncode==0,sent.stderr
        deadline=time.monotonic()+20
        while time.monotonic()<deadline and (not (work/'child_done.json').exists() or process_alive(ready['pid'])):time.sleep(.03)
        assert (work/'child_done.json').exists(),'Python cleanup incomplete'
        assert not process_alive(ready['pid']),'Python still alive'
        deadline=time.monotonic()+5
        while time.monotonic()<deadline and not prompt.exists():time.sleep(.03)
        assert prompt.exists(),'PowerShell did not return to a prompt'
        prompts=[json.loads(line) for line in prompt.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
        assert prompts and not any(p['python_alive'] for p in prompts),'PowerShell prompt returned before Python exit'
        assert all(p['parent_buffering_restored'] and p['cwd_unchanged'] for p in prompts),'Parent environment/CWD not restored'
        assert ps.poll() is None,'parent PowerShell did not stay alive'
        events=load(work/'events.json');names=[e['name'] for e in events]
        ordered=['SHUTDOWN_BEGIN','SHUTDOWN_AUDIT_WRITTEN','DISCONNECT_DONE','ALERTER_CLOSED','STORE_CLOSED','LOCK_RELEASED','CHILD_FINAL']
        assert [names.index(x) for x in ordered]==sorted(names.index(x) for x in ordered)
        assert names.count('SIGNAL')==1 and load(work/'child_done.json')['signals']==1
        assert prompts[0]['at']>=events[-1]['at']
        output=(work/'console.log').read_text(encoding='utf-8',errors='replace')
        assert 'Terminate batch job' not in output and 'Traceback' not in output
        return dict(shell=Path(shell).name,mode=mode,result='PASS',parent_ps_alive=True,python_exited_before_prompt=True,signal=load(signal_file),events=events,prompts=prompts,temp_path=str(work))
    finally:
        if ps.poll() is None:ps.terminate();ps.wait(timeout=10)
        log.close()
if __name__=='__main__':
    folder=Path(tempfile.mkdtemp(prefix='v6lite_B21_console_'));results=[]
    for shell in filter(None,[shutil.which('powershell.exe'),shutil.which('pwsh.exe')]):
        for mode in ['dry','paper_fake']:
            result=run_probe(shell,mode,folder/(Path(shell).stem+'_'+mode));results.append(result);print(json.dumps({k:result[k] for k in ('shell','mode','result','python_exited_before_prompt')},ensure_ascii=False),flush=True)
    target=ROOT/'docs/batch21_evidence/console_shutdown_results.json';target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(results,indent=2)+'\n',encoding='utf-8',newline='\n');print('CONSOLE_PROBES_COMPLETE',flush=True)
