"""Batch 17 G8: protected Windows process names require positive evidence."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from v6lite import approval

PID = 424242
PYTHON_PID = 424243


def _process(*, pid=PID, name=None, command=None):
    return SimpleNamespace(pid=pid, info={"pid": pid, "name": name, "cmdline": command})


def _install(monkeypatch, processes, snapshots=None, snapshot_error=None):
    monkeypatch.setattr(approval.psutil, "process_iter", lambda *args, **kwargs: iter(processes))
    calls = []
    values = snapshots if snapshots is not None else [{PID: "Secure System"}, {PID: "Secure System"}]

    def snapshot():
        calls.append(None)
        if snapshot_error is not None:
            raise snapshot_error
        return values[min(len(calls) - 1, len(values) - 1)]

    # The helper is intentionally absent on frozen Batch16/early Batch17.
    # This mock introduces only the proposed OS-evidence interface; the old
    # process detector must fail naturally on the protected-process cases.
    monkeypatch.setattr(approval, "_windows_process_names", snapshot, raising=False)
    return calls


def test_G8_Secure_System_missing_psutil_name_needs_two_stable_nonpython_snapshots(monkeypatch):
    process = _process(name=None, command=None)
    calls = _install(monkeypatch, [process])
    assert approval.old_v6_processes() == ()
    assert len(calls) >= 2


def test_G8_protected_nonpython_info_access_denied_can_be_proven_by_two_snapshots(monkeypatch):
    class ProtectedProcess:
        pid = PID

        @property
        def info(self):
            raise approval.psutil.AccessDenied(pid=PID, name="Secure System")

    calls = _install(monkeypatch, [ProtectedProcess()])
    assert approval.old_v6_processes() == ()
    assert len(calls) >= 2


def test_G8_normal_nonpython_needs_no_privileged_fallback(monkeypatch):
    calls = _install(monkeypatch, [_process(name="svchost.exe", command=None)], snapshot_error=AssertionError("unexpected fallback"))
    assert approval.old_v6_processes() == ()
    assert calls == []


def test_G8_protected_process_does_not_hide_later_real_old_V6(monkeypatch):
    processes = [_process(name=None, command=None),
                 _process(pid=PYTHON_PID, name="python.exe", command=["python.exe", "trading_bot_v6_main.py"])]
    calls = _install(monkeypatch, processes)
    assert approval.old_v6_processes() == (PYTHON_PID,)
    assert len(calls) >= 2


def test_G8_fallback_python_with_readable_old_entrypoint_is_detected(monkeypatch):
    process = _process(name=None, command=["python.exe", "auto_trading_runner.py"])
    _install(monkeypatch, [process], [{PID: "python.exe"}, {PID: "python.exe"}])
    assert approval.old_v6_processes() == (PID,)


@pytest.mark.parametrize("name", ["python.exe", "pythonw.exe", "py.exe"])
def test_G8_fallback_python_without_command_remains_fail_closed(monkeypatch, name):
    _install(monkeypatch, [_process(name=None, command=None)], [{PID: name}, {PID: name}])
    with pytest.raises(approval.GateError) as caught:
        approval.old_v6_processes()
    assert str(PID) in str(caught.value)


@pytest.mark.parametrize("snapshots", [
    [{}, {}],
    [{PID: ""}, {PID: ""}],
    [{PID: None}, {PID: None}],
    [{PID: "Secure System"}, {}],
    [{PID: "Secure System"}, {PID: "python.exe"}],
    [{PID: "Secure System"}, {PID: "svchost.exe"}],
    [None, None],
], ids=["missing_pid", "empty_name", "unreadable_name", "exit_between_snapshots",
        "pid_reused_for_python", "pid_identity_changed", "malformed_snapshot"])
def test_G8_unproven_fallback_identity_must_refuse(monkeypatch, snapshots):
    _install(monkeypatch, [_process(name=None, command=None)], snapshots)
    with pytest.raises(approval.GateError) as caught:
        approval.old_v6_processes()
    assert str(PID) in str(caught.value)


@pytest.mark.parametrize("error", [OSError("synthetic OS snapshot failed"), approval.psutil.AccessDenied(pid=PID)])
def test_G8_snapshot_failure_does_not_become_no_old_V6(monkeypatch, error):
    _install(monkeypatch, [_process(name=None, command=None)], snapshot_error=error)
    with pytest.raises(approval.GateError) as caught:
        approval.old_v6_processes()
    assert str(PID) in str(caught.value)


def test_G8_global_iterator_failure_cannot_be_cleared_by_known_nonpython_snapshot(monkeypatch):
    _install(monkeypatch, [])

    def failed_iterator(*args, **kwargs):
        raise approval.psutil.AccessDenied(pid=PID, name="Secure System")

    monkeypatch.setattr(approval.psutil, "process_iter", failed_iterator)
    with pytest.raises(approval.GateError) as caught:
        approval.old_v6_processes()
    assert str(PID) in str(caught.value)


def test_G8_exited_process_keeps_existing_NoSuchProcess_behavior(monkeypatch):
    class ExitedProcess:
        pid = PID

        @property
        def info(self):
            raise approval.psutil.NoSuchProcess(PID)

    calls = _install(monkeypatch, [ExitedProcess()], snapshot_error=AssertionError("unexpected fallback"))
    assert approval.old_v6_processes() == ()
    assert calls == []
