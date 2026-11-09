"""Keep offline acceptance subprocesses in one kill-on-close Windows job.

Source for API/layout semantics:
https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_extended_limit_information
Only the new acceptance child (and its descendants) is assigned. No existing
process is enumerated, terminated, or added to the job. A stdin gate prevents
pytest spawning descendants before successful assignment.
"""
import ctypes
from ctypes import wintypes
import os
import subprocess


class _Basic(ctypes.Structure):
    _fields_=[('PerProcessUserTimeLimit',ctypes.c_longlong),('PerJobUserTimeLimit',ctypes.c_longlong),
              ('LimitFlags',wintypes.DWORD),('MinimumWorkingSetSize',ctypes.c_size_t),
              ('MaximumWorkingSetSize',ctypes.c_size_t),('ActiveProcessLimit',wintypes.DWORD),
              ('Affinity',ctypes.c_size_t),('PriorityClass',wintypes.DWORD),('SchedulingClass',wintypes.DWORD)]


class _IO(ctypes.Structure):
    _fields_=[(name,ctypes.c_ulonglong) for name in ('ReadOperationCount','WriteOperationCount',
              'OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]


class _Extended(ctypes.Structure):
    _fields_=[('BasicLimitInformation',_Basic),('IoInfo',_IO),('ProcessMemoryLimit',ctypes.c_size_t),
              ('JobMemoryLimit',ctypes.c_size_t),('PeakProcessMemoryUsed',ctypes.c_size_t),
              ('PeakJobMemoryUsed',ctypes.c_size_t)]


class _Job:
    def __init__(self):
        if os.name!='nt': raise RuntimeError('B24 acceptance requires Windows')
        self.api=ctypes.WinDLL('kernel32',use_last_error=True)
        for name,result,args in (
            ('CreateJobObjectW',wintypes.HANDLE,[ctypes.c_void_p,wintypes.LPCWSTR]),
            ('SetInformationJobObject',wintypes.BOOL,[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]),
            ('AssignProcessToJobObject',wintypes.BOOL,[wintypes.HANDLE,wintypes.HANDLE]),
            ('TerminateJobObject',wintypes.BOOL,[wintypes.HANDLE,wintypes.UINT]),
            ('CloseHandle',wintypes.BOOL,[wintypes.HANDLE])):
            method=getattr(self.api,name);method.restype=result;method.argtypes=args
        self.handle=self.api.CreateJobObjectW(None,None)
        if not self.handle: raise ctypes.WinError(ctypes.get_last_error())
        info=_Extended();info.BasicLimitInformation.LimitFlags=0x2000
        if not self.api.SetInformationJobObject(self.handle,9,ctypes.byref(info),ctypes.sizeof(info)):
            error=ctypes.get_last_error();self.close();raise ctypes.WinError(error)

    def assign(self,process):
        if not self.api.AssignProcessToJobObject(self.handle,int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def terminate(self):
        if not self.api.TerminateJobObject(self.handle,124):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            handle,self.handle=self.handle,None
            if not self.api.CloseHandle(handle): raise ctypes.WinError(ctypes.get_last_error())


def run_guarded(args,*,cwd,env,timeout):
    job=_Job();process=None
    try:
        process=subprocess.Popen(args,cwd=cwd,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT,text=True,encoding='utf-8')
        job.assign(process)
        try:
            output,_=process.communicate(input='G',timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            job.terminate()
            output,_=process.communicate(timeout=10)
            raise subprocess.TimeoutExpired(args,timeout,output=output) from exc
        return subprocess.CompletedProcess(args,process.returncode,output)
    finally:
        # Failed assignment cannot release the child's stdin gate. Closing the
        # job kills any assigned children, including CREATE_NEW_CONSOLE probes.
        job.close()
        if process is not None and process.poll() is None:
            process.kill();process.communicate(timeout=10)
