"""Lightweight live gate + single-instance process lock.

Live trading needs ALL of:
  1. config file `mode = "live"` (live port, non-DU account, capital cap)
  2. the CLI flag `--live` on the `run` command
  3. an unexpired approval file bound to the account AND the config fingerprint
     (any parameter change invalidates it; max validity 30 days)

No environment variable can switch V6-Lite to live.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psutil

from v6lite.config import LiteConfig

MAX_APPROVAL_DAYS = 30
APPROVAL_FILE = "live_approval.json"


class GateError(RuntimeError):
    pass


_OLD_V6_ENTRYPOINTS = {"trading_bot_v6_main", "auto_trading_runner", "discord_bot_runner"}


def _old_v6_entrypoint(argv: list[str] | tuple[str, ...]) -> bool:
    """Inspect Python's script/module argument, not arbitrary command text."""
    index = 1
    while index < len(argv):
        arg = str(argv[index])
        if arg == "-m":
            return index + 1 < len(argv) and str(argv[index + 1]).split(".")[-1].lower() in _OLD_V6_ENTRYPOINTS
        if arg == "-c":
            return False
        if arg in {"-W", "-X"}:
            index += 2
            continue
        if arg.startswith("-"):
            index += 1
            continue
        return Path(arg).stem.lower() in _OLD_V6_ENTRYPOINTS
    return False


def _windows_process_names() -> dict[int, str]:
    """Toolhelp names do not require opening protected target processes.

    Unknown names remain unknown; this is evidence, not a system-PID whitelist.
    """
    if os.name != "nt":
        raise OSError("Windows process-name snapshot unavailable")
    import ctypes
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateToolhelp32Snapshot(0x00000002, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel.Process32FirstW(handle, ctypes.byref(entry)):
            raise ctypes.WinError(ctypes.get_last_error())
        result = {}
        while True:
            result[int(entry.th32ProcessID)] = str(entry.szExeFile)
            if not kernel.Process32NextW(handle, ctypes.byref(entry)):
                error = ctypes.get_last_error()
                if error != 18:  # ERROR_NO_MORE_FILES is the only successful end
                    raise ctypes.WinError(error)
                break
        return result
    finally:
        kernel.CloseHandle(handle)


def _verified_process_name(pid: int) -> str:
    """Two matching OS snapshots avoid trusting a vanished/reused PID's name."""
    try:
        first = _windows_process_names().get(pid)
        second = _windows_process_names().get(pid)
    except (psutil.Error, OSError, RuntimeError, AttributeError, TypeError, ValueError) as exc:
        raise GateError(f"cannot verify process {pid} name snapshot") from exc
    if not first or not second or str(first).lower() != str(second).lower():
        raise GateError(f"cannot verify process {pid} stable name")
    return str(second).lower()


def _python_process_name(name: str) -> bool:
    stem = Path(name).stem
    return stem.startswith("python") or stem == "py"


def old_v6_processes() -> tuple[int, ...]:
    """Read-only check. Proven non-Python is skipped; unknown Python refuses."""
    found: list[int] = []
    try:
        for process in psutil.process_iter(attrs=["pid", "name", "cmdline"]):
            try:
                info = process.info
                pid = int(info["pid"])
                if pid == os.getpid():
                    continue
                name = str(info.get("name") or "").lower()
                if not name:
                    name = _verified_process_name(pid)
                if not _python_process_name(name):
                    continue
                command = info.get("cmdline")
                if not command:
                    raise GateError(f"cannot verify Python process {pid} command")
                if _old_v6_entrypoint(command):
                    found.append(pid)
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied as exc:
                pid = getattr(process, "pid", None)
                if isinstance(pid, int):
                    name = _verified_process_name(pid)
                    if not _python_process_name(name):
                        continue
                # Never skip an unreadable Python or a PID with no name proof.
                raise GateError(f"cannot verify process {pid if isinstance(pid, int) else 'unknown'} "
                                "details (access denied)") from exc
    except (psutil.Error, OSError) as exc:
        pid = getattr(exc, "pid", None)
        raise GateError(f"cannot verify process {pid} details" if isinstance(pid, int)
                        else "cannot verify old V6 processes") from exc
    return tuple(sorted(found))


def write_approval(cfg: LiteConfig, *, operator: str, days: int, note: str, now: datetime) -> Path:
    if not cfg.is_live:
        raise GateError("approval only applies to a live config")
    if not operator.strip():
        raise GateError("operator name required")
    if not 1 <= days <= MAX_APPROVAL_DAYS:
        raise GateError(f"days must be 1..{MAX_APPROVAL_DAYS}")
    record = {
        "account": cfg.account,
        "config_fingerprint": cfg.fingerprint(),
        "max_capital_usd": cfg.max_capital_usd,
        "max_positions": cfg.max_positions,
        "daily_loss_limit_usd": cfg.daily_loss_limit_usd,
        "tradable_symbols": list(cfg.tradable_symbols),
        "approved_by": operator.strip(),
        "approved_at": now.isoformat(),
        "expires_at": (now + timedelta(days=days)).isoformat(),
        "note": note,
    }
    path = cfg.resolved_state_dir() / APPROVAL_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return path


def check_live_gate(cfg: LiteConfig, *, live_flag: bool, now: datetime) -> dict | None:
    """Return the approval record for live mode; None for paper. Raise on any failure."""
    if not cfg.is_live:
        if live_flag:
            raise GateError("--live given but config mode is paper")
        return None
    if not live_flag:
        raise GateError("live config requires the explicit --live flag")
    path = cfg.resolved_state_dir() / APPROVAL_FILE
    if not path.exists():
        raise GateError(f"no live approval at {path}; run `approve-live` first")
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("account") != cfg.account:
        raise GateError("approval account does not match config")
    if record.get("config_fingerprint") != cfg.fingerprint():
        raise GateError("config changed since approval (fingerprint mismatch); re-approve")
    expires = datetime.fromisoformat(record["expires_at"])
    if now >= expires:
        raise GateError(f"live approval expired at {record['expires_at']}")
    return record


def revoke_approval(cfg: LiteConfig) -> bool:
    path = cfg.resolved_state_dir() / APPROVAL_FILE
    if path.exists():
        path.unlink()
        return True
    return False


# ---------------------------------------------------------------- account lock
def _pid_alive(pid: int) -> bool:
    """True if pid may be a live trading process.

    Only proof that the process is gone (NoSuchProcess) or that the pid was
    reused by a non-Python program returns False (psutil's ZombieProcess is a
    NoSuchProcess: the process has exited). When the state cannot be
    determined (AccessDenied or any other error) the answer is True so the
    caller fails closed."""
    try:
        import psutil
    except ImportError:  # pragma: no cover
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True
    try:
        return "python" in psutil.Process(pid).name().lower()
    except psutil.NoSuchProcess:
        return False
    except Exception:  # AccessDenied, ...: cannot prove it is gone
        return True


def _lock_pid(path: Path) -> int | None:
    """Understands both legacy V6 lock formats: JSON {"pid": ...} and
    "pid\ntimestamp". Unknown content -> None (callers fail closed)."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return None
    if text.startswith("{"):
        try:
            value = json.loads(text).get("pid")
        except (ValueError, AttributeError):
            return None
        return value if type(value) is int else None
    try:
        return int(text.split()[0])
    except (IndexError, ValueError):
        return None


def default_lock_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) / "v6lite" / "locks") if base else (Path.home() / ".v6lite" / "locks")


def _os_lock(handle) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _os_unlock(handle) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class AccountLock:
    """One V6-Lite process per broker account, independent of state_dir.

    Uses an OS file lock (msvcrt on Windows, flock elsewhere): acquisition is
    atomic, only the holder can release it, and a crashed holder releases it
    automatically, so there is no stale-lock cleanup to get wrong.
    Legacy V6 does not know this lock; its lock files are checked one-way and
    the operator must keep the legacy entry points stopped.
    """

    def __init__(self, key: str, *, lock_dir: Path | None = None):
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in key)
        if not safe:
            raise GateError("empty lock key")
        self.path = (lock_dir or default_lock_dir()) / f"{safe}.lock"
        self._handle = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self, foreign: tuple[str, ...] = ()) -> None:
        for other in foreign:
            other_path = Path(other).expanduser()
            if other_path.exists():
                pid = _lock_pid(other_path)
                if pid is None or _pid_alive(pid):
                    raise GateError(f"another trading process holds {other_path} (pid {pid}); "
                                    "never run V6 and V6-Lite against the same account")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            _os_lock(handle)
        except OSError:
            handle.close()
            raise GateError(f"account lock {self.path.name} is held by another V6-Lite process") from None
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n{datetime.now(timezone.utc).isoformat()}\n".encode())
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            _os_unlock(self._handle)
        finally:
            self._handle.close()
            self._handle = None
