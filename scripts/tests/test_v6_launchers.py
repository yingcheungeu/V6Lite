"""Batch 18 operational launcher tests; every runtime/config/state is TEMP.

PowerShell 5.1 and the real cmd wrapper are exercised. No test runs the V6-Lite
CLI, creates a broker, reads a real User webhook, or accesses operator state.
Mocked CLI dispatch records argv only. The actual Python helper is restricted
to a tiny print/exit child, or its embedded config / SQLite mode=ro program.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "v6.ps1"
CMD = ROOT / "scripts" / "v6.cmd"
POWERSHELL = shutil.which("powershell.exe")
SYNTHETIC_WEBHOOK = "synthetic-webhook-secret-never-print"
MODES = ("dry", "g3t-dry", "g3t", "g3n-dry", "g3n")
pytestmark = pytest.mark.skipif(
    os.name != "nt" or not POWERSHELL, reason="Windows PowerShell 5.1 required"
)


def ps_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def run_process(argv, *, cwd=None):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        argv, cwd=cwd, env=env, text=True, encoding="utf-8", errors="replace",
        capture_output=True, timeout=45,
    )


def git(root, *args):
    completed = run_process(["git", "--no-optional-locks", "-C", str(root), *args])
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def commit_fixture(root):
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Launcher Test")
    git(root, "config", "user.email", "launcher-test@example.invalid")
    git(root, "config", "core.autocrlf", "false")
    git(root, "add", "v6lite", ".gitignore")
    git(root, "commit", "-m", "TEMP launcher fixture")
    git(root, "tag", "-a", "v6lite-run-fixture", "-m", "TEMP annotated run tag")


@pytest.fixture
def runtime_repo(tmp_path):
    root = tmp_path / "runtime repo"
    (root / "v6lite").mkdir(parents=True)
    (root / "v6lite" / "__init__.py").write_text("# TEMP only\n", encoding="utf-8")
    (root / "v6lite" / "marker.py").write_text("MARKER = 1\n", encoding="utf-8")
    (root / ".gitignore").write_text(
        ".venv/\n/*.toml\nstate/\n", encoding="utf-8"
    )
    for name in ("paper", "g3t", "g3n"):
        (root / f"v6lite.{name}.toml").write_text("# mocked TEMP config\n")
    python = root / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"TEMP mock leaf; not executable")
    commit_fixture(root)
    return root


def run_ps(tmp_path, source):
    harness = tmp_path / "launcher_harness.ps1"
    harness.write_text(
        "$OutputEncoding = [Console]::OutputEncoding = "
        "[Text.UTF8Encoding]::new($false)\n" + source,
        encoding="utf-8-sig",
    )
    completed = run_process([
        POWERSHELL, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(harness),
    ])
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    result_lines = [
        line[len("RESULT="):] for line in completed.stdout.splitlines()
        if line.startswith("RESULT=")
    ]
    assert len(result_lines) == 1, (completed.stdout, completed.stderr)
    return json.loads(result_lines[0]), completed


def launcher(tmp_path, runtime_repo, action="dry", target="", **overrides):
    case = {
        "action": action, "target": target, "unexpected": [], "whatif": False,
        "webhook": SYNTHETIC_WEBHOOK, "interactive": True, "answer": "YES",
        "exitcode": 0, "config": {}, "config_changes": False,
        "dirty_during_confirmation": False, "status_exit": 0,
        "status_output": json.dumps({"state": "NO_STATE", "read_only": True}),
    }
    case.update(overrides)
    payload = base64.b64encode(json.dumps(case).encode()).decode()
    source = r"""
. __SCRIPT__
$script:V6RepositoryRoot = __ROOT__
$script:fixture = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__PAYLOAD__')) | ConvertFrom-Json
$script:dispatches = [Collections.Generic.List[object]]::new()
$script:confirmations = 0
$script:interactiveChecks = 0
$script:probes = 0
$script:beforeWebhook = [Environment]::GetEnvironmentVariable('V6LITE_DISCORD_WEBHOOK','Process')
$script:beforeBytecode = [Environment]::GetEnvironmentVariable('PYTHONDONTWRITEBYTECODE','Process')
function Get-V6UserWebhook { return $script:fixture.webhook }
function Assert-V6Interactive {
    $script:interactiveChecks++
    if (-not $script:fixture.interactive) { Stop-V6 'INTERACTIVE_CONSOLE_REQUIRED' }
}
function Read-V6Confirmation {
    $script:confirmations++
    if ($script:fixture.dirty_during_confirmation) {
        [IO.File]::WriteAllText((Join-Path $script:V6RepositoryRoot 'v6lite\paused.py'), 'TEMP dirty')
    }
    return $script:fixture.answer
}
function Invoke-V6Python {
    param([string[]]$Arguments, [switch]$Capture, [string]$InputCode)
    if ($Capture -and $Arguments.Count -gt 2 -and $Arguments[2] -ceq 'probe') {
        if ($InputCode -cne $script:V6ReadOnlyProgram) { throw 'Probe helper source was not passed through InputCode' }
        $script:probes++
        $isTest = [IO.Path]::GetFileName($Arguments[3]) -ceq 'v6lite.g3t.toml'
        $cfg = [ordered]@{
            mode='paper';paper_account=$true;host='127.0.0.1';port=7497
            client_id=$(if ($isTest) {132} else {131})
            test_enabled=$isTest;test_qty=3;protection_enabled=$true
            market_data_type=1;webhook_env='V6LITE_DISCORD_WEBHOOK'
            tradable=@('AAPL','TSLA','BRK.B')
            code_sha='0123456789abcdef';tag_code_sha='0123456789abcdef'
            config_sha256=('a' * 64)
        }
        foreach ($property in $script:fixture.config.psobject.Properties) { $cfg[$property.Name]=$property.Value }
        if ($script:fixture.config_changes -and $script:probes -gt 1) { $cfg.config_sha256=('b' * 64) }
        return [pscustomobject]@{ExitCode=0;Output=($cfg | ConvertTo-Json -Compress)}
    }
    $script:dispatches.Add([pscustomobject]@{
        argv=@($Arguments);capture=[bool]$Capture;input_code=$InputCode
        webhook_loaded=([Environment]::GetEnvironmentVariable('V6LITE_DISCORD_WEBHOOK','Process') -ceq $script:fixture.webhook)
        no_bytecode=([Environment]::GetEnvironmentVariable('PYTHONDONTWRITEBYTECODE','Process') -ceq '1')
    })
    if ($Capture) { return [pscustomobject]@{ExitCode=$script:fixture.status_exit;Output=$script:fixture.status_output} }
    return [int]$script:fixture.exitcode
}
$resultCode=$null
$safeReason=$null
try {
    $resultCode=Invoke-V6Launcher -Action $script:fixture.action -Target $script:fixture.target -Unexpected @($script:fixture.unexpected) -WhatIf:$script:fixture.whatif
} catch {
    $safeReason=$_.Exception.Message
    if ($safeReason -notmatch '^V6_[A-Z0-9_]+$') { $safeReason='HARNESS_UNEXPECTED_EXCEPTION:' + $_.Exception.GetType().Name }
}
$result = @{
    code=$resultCode;error=$safeReason;calls=@($script:dispatches.ToArray())
    confirmations=$script:confirmations;interactive_checks=$script:interactiveChecks
    webhook_restored=([Environment]::GetEnvironmentVariable('V6LITE_DISCORD_WEBHOOK','Process') -ceq $script:beforeWebhook)
    bytecode_restored=([Environment]::GetEnvironmentVariable('PYTHONDONTWRITEBYTECODE','Process') -ceq $script:beforeBytecode)
}
Write-Host ('RESULT=' + ($result | ConvertTo-Json -Depth 8 -Compress))
"""
    source = source.replace("__SCRIPT__", ps_literal(SCRIPT))
    source = source.replace("__ROOT__", ps_literal(runtime_repo))
    source = source.replace("__PAYLOAD__", payload)
    result, completed = run_ps(tmp_path, source)
    assert SYNTHETIC_WEBHOOK not in completed.stdout + completed.stderr
    assert result["webhook_restored"] and result["bytecode_restored"]
    return result, completed


def config_name(mode):
    return "g3t" if mode.startswith("g3t") else "g3n" if mode.startswith("g3n") else "paper"


@pytest.mark.parametrize("mode", MODES)
def test_modes_dispatch_exact_cli_argv_without_executing_cli(tmp_path, runtime_repo, mode):
    result, completed = launcher(tmp_path, runtime_repo, mode, exitcode=7)
    assert result["error"] is None
    assert result["code"] == 7
    assert len(result["calls"]) == 1
    call = result["calls"][0]
    expected = ["-B", "-m", "v6lite", "run", "--config",
                str(runtime_repo / f"v6lite.{config_name(mode)}.toml")]
    dry = mode == "dry" or mode.endswith("-dry")
    if dry:
        expected += ["--dry-run"]
    assert call["argv"] == expected
    assert not call["capture"] and call["webhook_loaded"] and call["no_bytecode"]
    assert result["confirmations"] == result["interactive_checks"] == (0 if dry else 1)
    assert f"Webhook User exists=True length={len(SYNTHETIC_WEBHOOK)}" in completed.stdout


@pytest.mark.parametrize("mode", MODES)
def test_whatif_is_plan_only_and_never_prompts_or_dispatches(tmp_path, runtime_repo, mode):
    result, completed = launcher(tmp_path, runtime_repo, mode, whatif=True, answer="no")
    assert result["code"] == 0 and result["error"] is None
    assert result["calls"] == [] and result["confirmations"] == 0
    assert result["interactive_checks"] == 0
    plan = json.loads(next(line[5:] for line in completed.stdout.splitlines() if line.startswith("PLAN=")))
    assert plan["mode"] == mode
    assert plan["config"] == f"v6lite.{config_name(mode)}.toml"
    assert ("--dry-run" in plan["argv"]) == (mode == "dry" or mode.endswith("-dry"))


def test_entry_uppercases_symbol_and_only_uses_g3t_operator_john(tmp_path, runtime_repo):
    result, _ = launcher(tmp_path, runtime_repo, "entry", "brk.b")
    assert result["code"] == 0 and result["error"] is None
    assert result["calls"][0]["argv"] == [
        "-B", "-m", "v6lite", "test-entry", "BRK.B", "--config",
        str(runtime_repo / "v6lite.g3t.toml"), "--operator", "John",
    ]
    assert result["confirmations"] == result["interactive_checks"] == 1


@pytest.mark.parametrize("target,reason", [
    ("", "ENTRY_SYMBOL_INVALID"), ("AAPL;", "ENTRY_SYMBOL_INVALID"),
    ("MSTR", "ENTRY_SYMBOL_NOT_TRADABLE"), ("AAPL extra", "ENTRY_SYMBOL_INVALID"),
])
def test_entry_refuses_invalid_or_untradable_symbol(tmp_path, runtime_repo, target, reason):
    result, _ = launcher(tmp_path, runtime_repo, "entry", target)
    assert result["error"] == f"V6_{reason}" and result["calls"] == []


@pytest.mark.parametrize("target", ("", *MODES))
def test_status_default_dry_or_selected_mode_uses_readonly_program(tmp_path, runtime_repo, target):
    result, _ = launcher(tmp_path, runtime_repo, "status", target, webhook=None)
    assert result["code"] == 0 and result["error"] is None
    assert result["confirmations"] == result["interactive_checks"] == 0
    call = result["calls"][0]
    mode = target or "dry"
    assert call["capture"]
    assert call["argv"][:2] == ["-B", "-"]
    assert "mode=ro" in call["input_code"]
    assert call["argv"][2:] == [
        "status", str(runtime_repo / f"v6lite.{config_name(mode)}.toml"),
        "true" if mode == "dry" or mode.endswith("-dry") else "false",
    ]
    assert "-m" not in call["argv"] and "--dry-run" not in call["argv"]


@pytest.mark.parametrize("answer", ("yes", "Yes", "Y", " YES", "YES ", ""))
@pytest.mark.parametrize("action,target", (("g3t", ""), ("g3n", ""), ("entry", "AAPL"), ("tptimeout", "AAPL")))
def test_order_capable_actions_require_exact_yes(tmp_path, runtime_repo, action, target, answer):
    result, _ = launcher(tmp_path, runtime_repo, action, target, answer=answer)
    assert result["error"] == "V6_YES_CONFIRMATION_REQUIRED"
    assert result["confirmations"] == 1 and result["calls"] == []


def test_interactive_console_gate_is_mandatory(tmp_path, runtime_repo):
    result, _ = launcher(tmp_path, runtime_repo, "g3t", interactive=False)
    assert result["error"] == "V6_INTERACTIVE_CONSOLE_REQUIRED"
    assert result["confirmations"] == 0 and result["calls"] == []


@pytest.mark.parametrize("mode", ("g3t", "g3n", "g3t-dry", "g3n-dry"))
@pytest.mark.parametrize("port", (4001, 4002, 7496, 7498))
def test_g3_modes_refuse_every_non_7497_port(tmp_path, runtime_repo, mode, port):
    result, _ = launcher(tmp_path, runtime_repo, mode, config={"port": port})
    assert result["error"] == "V6_TWS_PAPER_7497_REQUIRED"
    assert result["calls"] == [] and result["confirmations"] == 0


@pytest.mark.parametrize("action,config,reason", [
    ("g3t", {"test_enabled": False}, "G3T_IDENTITY_REQUIRED"),
    ("g3t", {"client_id": 131}, "G3T_IDENTITY_REQUIRED"),
    ("g3t", {"test_qty": 4}, "G3T_IDENTITY_REQUIRED"),
    ("g3n", {"test_enabled": True}, "G3N_IDENTITY_REQUIRED"),
    ("g3n", {"client_id": 132}, "G3N_IDENTITY_REQUIRED"),
    ("g3t", {"protection_enabled": False}, "G3_PROTECTION_REALTIME_REQUIRED"),
    ("g3t", {"market_data_type": 3}, "G3_PROTECTION_REALTIME_REQUIRED"),
    ("dry", {"mode": "live"}, "PAPER_LOCALHOST_REQUIRED"),
    ("dry", {"paper_account": False}, "PAPER_LOCALHOST_REQUIRED"),
    ("dry", {"host": "remote.invalid"}, "PAPER_LOCALHOST_REQUIRED"),
    ("dry", {"webhook_env": "OTHER_KEY"}, "USER_WEBHOOK_KEY_REQUIRED"),
    ("dry", {"code_sha": "x" * 16}, "PACKAGE_HASH_MISMATCH"),
    ("dry", {"tag_code_sha": "fedcba9876543210"}, "PACKAGE_HASH_MISMATCH"),
])
def test_config_identity_and_safety_fail_closed(tmp_path, runtime_repo, action, config, reason):
    result, _ = launcher(tmp_path, runtime_repo, action, config=config)
    assert result["error"] == f"V6_{reason}" and result["calls"] == []


@pytest.mark.parametrize("dirty", ("tracked", "untracked"))
def test_real_git_refuses_dirty_package(tmp_path, runtime_repo, dirty):
    path = runtime_repo / "v6lite" / ("marker.py" if dirty == "tracked" else "extra.py")
    path.write_text("CHANGED = True\n")
    result, _ = launcher(tmp_path, runtime_repo)
    assert result["error"] == "V6_PACKAGE_DIRTY" and result["calls"] == []


@pytest.mark.parametrize("kind", ("lightweight", "head_moved", "wrong_branch"))
def test_real_git_requires_main_and_annotated_run_tag_at_head(tmp_path, runtime_repo, kind):
    if kind == "lightweight":
        git(runtime_repo, "tag", "-d", "v6lite-run-fixture")
        git(runtime_repo, "tag", "v6lite-run-lightweight")
    elif kind == "head_moved":
        git(runtime_repo, "commit", "--allow-empty", "-m", "moved HEAD")
    else:
        git(runtime_repo, "switch", "-c", "dev")
    result, _ = launcher(tmp_path, runtime_repo)
    reason = "RUNTIME_MAIN_REQUIRED" if kind == "wrong_branch" else "ANNOTATED_RUN_TAG_REQUIRED"
    assert result["error"] == f"V6_{reason}" and result["calls"] == []


@pytest.mark.parametrize("change", ("config", "package"))
def test_post_confirmation_rechecks_mutable_evidence(tmp_path, runtime_repo, change):
    result, _ = launcher(
        tmp_path, runtime_repo, "g3t", config_changes=change == "config",
        dirty_during_confirmation=change == "package",
    )
    reason = "EVIDENCE_CHANGED" if change == "config" else "PACKAGE_DIRTY"
    assert result["error"] == f"V6_{reason}"
    assert result["confirmations"] == 1 and result["calls"] == []


def test_missing_user_webhook_blocks_run_but_not_preview(tmp_path, runtime_repo):
    refused, _ = launcher(tmp_path, runtime_repo, webhook=None)
    assert refused["error"] == "V6_USER_WEBHOOK_MISSING" and refused["calls"] == []
    preview, _ = launcher(tmp_path, runtime_repo, "g3t", webhook=None, whatif=True)
    assert preview["code"] == 0 and preview["calls"] == []


@pytest.mark.parametrize("status_exit,status_output", ((2, "{}"), (0, "not JSON")))
def test_status_child_failure_is_refused_and_process_env_restored(
        tmp_path, runtime_repo, status_exit, status_output):
    result, _ = launcher(
        tmp_path, runtime_repo, "status", "g3t",
        status_exit=status_exit, status_output=status_output,
    )
    assert result["error"] == "V6_READ_ONLY_STATUS_REFUSED"


@pytest.mark.parametrize("action,target,unexpected,reason", [
    ("unknown", "", [], "USAGE_V6_MODE_OR_ENTRY_OR_STATUS"),
    ("G3T", "", [], "USAGE_V6_MODE_OR_ENTRY_OR_STATUS"),
    ("g3t", "AAPL", [], "EXTRA_ARGUMENTS_REFUSED"),
    ("entry", "AAPL", ["g3n"], "EXTRA_ARGUMENTS_REFUSED"),
    ("status", "bad", [], "STATUS_MODE_INVALID"),
])
def test_bad_arguments_fail_closed(tmp_path, runtime_repo, action, target, unexpected, reason):
    result, _ = launcher(tmp_path, runtime_repo, action, target, unexpected=unexpected)
    assert result["error"] == f"V6_{reason}" and result["calls"] == []


def test_real_top_level_invalid_action_has_safe_error_and_exit():
    completed = run_process([
        POWERSHELL, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(SCRIPT), "not-a-mode",
    ])
    assert completed.returncode == 2
    assert completed.stderr.strip() == "V6 refused: V6_USAGE_V6_MODE_OR_ENTRY_OR_STATUS"
    assert SYNTHETIC_WEBHOOK not in completed.stdout + completed.stderr


@pytest.mark.parametrize("child_exit", (0, 7))
def test_real_cmd_wrapper_forwards_arguments_and_child_exit(tmp_path, child_exit):
    folder = tmp_path / "wrapper with spaces"
    folder.mkdir()
    shutil.copy2(CMD, folder / "v6.cmd")
    (folder / "v6.ps1").write_text(
        "$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)\n"
        "Write-Host ('ARGS=' + (@($args) | ConvertTo-Json -Compress))\n"
        f"exit {child_exit}\n", encoding="utf-8-sig"
    )
    arguments = ["entry", "BRK.B", "two words", "trailing"]
    command = "call " + subprocess.list2cmdline([str(folder / "v6.cmd"), *arguments])
    completed = run_process(f'cmd.exe /d /s /c "{command}"')
    assert completed.returncode == child_exit, (completed.stdout, completed.stderr)
    rendered = next(line[5:] for line in completed.stdout.splitlines() if line.startswith("ARGS="))
    assert json.loads(rendered) == arguments


def clone_temp_venv(root):
    python = root / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sys.executable, python)
    (root / ".venv" / "pyvenv.cfg").write_text(
        f"home = {sys.base_prefix}\ninclude-system-site-packages = true\nversion = 3.11\n",
        encoding="utf-8"
    )
    return python


@pytest.mark.parametrize("child_exit", (0, 7))
def test_real_python_helper_separates_stdout_from_integer_exit(tmp_path, child_exit):
    root = tmp_path / "tiny python root"
    root.mkdir()
    clone_temp_venv(root)
    child = f'import sys; print("V6_STUB_STDOUT"); sys.exit({child_exit})'
    child_path = root / "tiny_print_exit.py"
    child_path.write_text(child, encoding="utf-8")
    source = r"""
. __SCRIPT__
$script:V6RepositoryRoot=__ROOT__
$values=@(Invoke-V6Python -Arguments @('-B',__CHILD__))
$result=@{count=$values.Count;values=@($values);types=@($values | ForEach-Object {$_.GetType().FullName})}
Write-Host ('RESULT=' + ($result | ConvertTo-Json -Depth 4 -Compress))
""".replace("__SCRIPT__", ps_literal(SCRIPT)).replace("__ROOT__", ps_literal(root)).replace("__CHILD__", ps_literal(child_path))
    result, completed = run_ps(tmp_path, source)
    assert result["count"] == 1, "Child stdout must not contaminate the function exit-code pipeline"
    assert result["values"] == [child_exit] and result["types"] == ["System.Int32"]
    assert "V6_STUB_STDOUT" in completed.stdout


@pytest.fixture(scope="session")
def embedded_program(tmp_path_factory):
    folder = tmp_path_factory.mktemp("embedded_program")
    source = r"""
. __SCRIPT__
Write-Host ('RESULT=' + (@{program=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($script:V6ReadOnlyProgram))} | ConvertTo-Json -Compress))
""".replace("__SCRIPT__", ps_literal(SCRIPT))
    result, _ = run_ps(folder, source)
    return base64.b64decode(result["program"]).decode("utf-8")


@pytest.fixture
def actual_repo(tmp_path):
    root = tmp_path / "actual package TEMP"
    root.mkdir()
    shutil.copytree(
        ROOT / "v6lite", root / "v6lite",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (root / ".gitignore").write_text(".venv/\n/*.toml\nstate/\nstate-test/\nv6lite/ignored-extra.py\n")
    commit_fixture(root)
    clone_temp_venv(root)
    return root


def actual_config(root, *, test=False):
    path = root / ("v6lite.g3t.toml" if test else "v6lite.paper.toml")
    state = root / ("state-test" if test else "state")
    path.write_text(
        'mode = "paper"\n[ibkr]\nhost = "127.0.0.1"\nport = 7497\n'
        f'client_id = {132 if test else 131}\naccount = "DU0000000"\n'
        'market_data_type = 1\n[universe]\ntradable = ["AAPL"]\nscan = ["AAPL"]\n'
        f'[paths]\nstate_dir = "{state.as_posix()}"\n'
        f'[test]\nenabled = {"true" if test else "false"}\nqty = 3\n',
        encoding="utf-8"
    )
    return path


def read_only_run(root, program, cfg, operation, dry):
    completed = run_process([
        sys.executable, "-B", "-c", program, operation, str(cfg),
        "true" if dry else "false",
    ], cwd=root)
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        pytest.fail(f"Embedded helper did not return JSON: {completed.stdout!r}")
    return result, completed


def create_db(root, *, dry=False, test=False, meta_override=None, health=None):
    base = root / ("state-test" if test else "state")
    folder = base / "dryrun" if dry else base
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "v6lite.sqlite3"
    meta = {
        "account": "DU0000000", "mode": "paper",
        "role": "dryrun" if dry else "test" if test else "formal",
        "schema_version": "4", "client_id": "132" if test else "131",
    }
    meta.update(meta_override or {})
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE positions(symbol TEXT,qty_open INTEGER,stop REAL,status TEXT);
            CREATE TABLE intents(status TEXT);
            CREATE TABLE controls(key TEXT PRIMARY KEY,value TEXT);
        """)
        connection.executemany("INSERT INTO meta VALUES (?,?)", list(meta.items()))
        connection.execute("INSERT INTO positions VALUES ('AAPL',3,97.0,'OPEN')")
        connection.execute("INSERT INTO intents VALUES ('SENT')")
        connection.executemany("INSERT INTO controls VALUES (?,?)", [
            ("kill", "true"), ("loss_streak", "2"), ("anomaly:fixture", "TEMP"),
        ])
        if health is not None:
            connection.execute("INSERT INTO controls VALUES ('health',?)", (json.dumps(health),))
    return path


def files_snapshot(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file() and ".git" not in path.relative_to(root).parts
    }


@pytest.mark.parametrize("dry,test", ((True, False), (False, False), (False, True)))
def test_readonly_status_uses_exact_role_and_client_without_writes(
        actual_repo, embedded_program, dry, test):
    cfg = actual_config(actual_repo, test=test)
    db = create_db(actual_repo, dry=dry, test=test)
    before = files_snapshot(actual_repo)
    result, completed = read_only_run(actual_repo, embedded_program, cfg, "status", dry)
    assert completed.returncode == 0 and result["state"] == "OPENED_RO"
    assert result["role"] == ("dryrun" if dry else "test" if test else "formal")
    assert result["client_id"] == (132 if test else 131)
    assert result["api_client_id"] == ((132 if test else 131) + (1000 if dry else 0))
    assert result["read_only"] is True
    assert result["open_positions"] == [{"symbol": "AAPL", "qty_open": 3, "stop": 97.0}]
    assert result["active_order_count"] == 1 and result["kill"] is True
    assert result["loss_streak"] == 2 and result["anomaly_count"] == 1
    assert files_snapshot(actual_repo) == before
    assert db.is_file()


@pytest.mark.parametrize("dry", (True, False))
def test_missing_state_status_does_not_create_state_directory(actual_repo, embedded_program, dry):
    cfg = actual_config(actual_repo)
    before = files_snapshot(actual_repo)
    result, completed = read_only_run(actual_repo, embedded_program, cfg, "status", dry)
    assert completed.returncode == 0 and result["state"] == "NO_STATE"
    assert not (actual_repo / "state").exists()
    assert files_snapshot(actual_repo) == before


@pytest.mark.parametrize("override", (
    {"account": "DU0000000"}, {"mode": "live"}, {"role": "formal"},
    {"schema_version": "3"}, {"client_id": "999"},
))
def test_status_wrong_state_identity_fails_without_writes(actual_repo, embedded_program, override):
    cfg = actual_config(actual_repo)
    create_db(actual_repo, dry=True, meta_override=override)
    before = files_snapshot(actual_repo)
    result, completed = read_only_run(actual_repo, embedded_program, cfg, "status", True)
    assert completed.returncode == 2
    assert result == {"error": "READ_ONLY_CHECK_FAILED"}
    assert files_snapshot(actual_repo) == before


def test_dry_status_never_reads_formal_state(actual_repo, embedded_program):
    cfg = actual_config(actual_repo)
    create_db(actual_repo, dry=False, meta_override={"account": "DU0000000"})
    result, completed = read_only_run(actual_repo, embedded_program, cfg, "status", True)
    assert completed.returncode == 0 and result["state"] == "NO_STATE"
    assert not (actual_repo / "state" / "dryrun").exists()


def test_status_preserves_actual_engine_health_shape(actual_repo, embedded_program):
    cfg = actual_config(actual_repo)
    # Keys come from Engine.status_snapshot, not the launcher's own assumptions.
    health = {
        "at": datetime.now(timezone.utc).isoformat(),
        "broker_connected": True, "reconcile_ok": False, "entries_allowed": False,
        "entry_blocks": ["TEMP block 1", "TEMP block 2"],
    }
    create_db(actual_repo, dry=True, health=health)
    before = files_snapshot(actual_repo)
    result, completed = read_only_run(actual_repo, embedded_program, cfg, "status", True)
    assert completed.returncode == 0
    rendered = result["engine_health"]
    assert rendered["broker_connected"] is True
    assert rendered["reconcile_ok"] is False and rendered["entries_allowed"] is False
    assert rendered["freshness"] == "fresh" and rendered["entry_block_count"] == 2
    assert files_snapshot(actual_repo) == before


def test_real_probe_compares_worktree_bytes_against_committed_bytes(actual_repo, embedded_program):
    cfg = actual_config(actual_repo)
    original, completed = read_only_run(actual_repo, embedded_program, cfg, "probe", False)
    assert completed.returncode == 0
    assert original["code_sha"] == original["tag_code_sha"]
    assert original["config_sha256"] == hashlib.sha256(cfg.read_bytes()).hexdigest()
    assert original["client_id"] == 131 and original["paper_account"]
    (actual_repo / "v6lite" / "ignored-extra.py").write_text("# ignored current package addition\n")
    assert git(actual_repo, "status", "--porcelain", "--untracked-files=all", "--", "v6lite") == ""
    changed, completed = read_only_run(actual_repo, embedded_program, cfg, "probe", False)
    assert completed.returncode == 0
    assert changed["code_sha"] != changed["tag_code_sha"]
    assert changed["tag_code_sha"] == original["tag_code_sha"]


def test_ps51_actual_configuration_probe_keeps_native_argument_quotes(
        tmp_path, actual_repo):
    cfg = actual_config(actual_repo)
    before = files_snapshot(actual_repo)
    source = r"""
. __SCRIPT__
$script:V6RepositoryRoot=__ROOT__
Push-Location -LiteralPath $script:V6RepositoryRoot
try {
    $errorReason=$null
    $configuration=$null
    try { $configuration=Get-V6Configuration __CFG__ }
    catch { $errorReason=$_.Exception.Message }
    Write-Host ('RESULT=' + (@{error=$errorReason;cfg=$configuration} | ConvertTo-Json -Depth 6 -Compress))
} finally { Pop-Location }
""".replace("__SCRIPT__", ps_literal(SCRIPT)).replace("__ROOT__", ps_literal(actual_repo)).replace("__CFG__", ps_literal(cfg))
    result, _ = run_ps(tmp_path, source)
    assert result["error"] is None, "PS 5.1 stdin transport must preserve embedded Python source"
    assert result["cfg"]["code_sha"] == result["cfg"]["tag_code_sha"]
    assert result["cfg"]["client_id"] == 131
    assert files_snapshot(actual_repo) == before


@pytest.mark.parametrize("target,dry,test", (("", True, False), ("g3t", False, True)))
def test_ps51_actual_status_uses_embedded_program_and_readonly_database(
        tmp_path, actual_repo, target, dry, test):
    cfg = actual_config(actual_repo, test=test)
    create_db(actual_repo, dry=dry, test=test)
    before = files_snapshot(actual_repo)
    source = r"""
. __SCRIPT__
$script:V6RepositoryRoot=__ROOT__
function Get-V6UserWebhook { return $null }
function Assert-V6Interactive { throw 'Unexpected real status prompt' }
function Read-V6Confirmation { throw 'Unexpected real status prompt' }
$reason=$null
$exitResult=$null
try { $exitResult=Invoke-V6Launcher -Action 'status' -Target __TARGET__ }
catch { $reason=$_.Exception.Message }
Write-Host ('RESULT=' + (@{error=$reason;code=$exitResult} | ConvertTo-Json -Depth 4 -Compress))
""".replace("__SCRIPT__", ps_literal(SCRIPT)).replace("__ROOT__", ps_literal(actual_repo)).replace("__TARGET__", ps_literal(target))
    result, completed = run_ps(tmp_path, source)
    assert result["error"] is None, "Actual PS 5.1 probe/status must work without a CLI or broker"
    assert result["code"] == 0
    assert '"OPENED_RO"' in completed.stdout and '"read_only": true' in completed.stdout
    assert f'"role": "{("dryrun" if dry else "test")}"' in completed.stdout
    assert files_snapshot(actual_repo) == before

def test_real_interactive_guard_refuses_redirected_stdin(tmp_path):
    """Exercise the actual Console predicate; no launcher or User secret read."""
    harness = tmp_path / "real_interactive_guard.ps1"
    source = r"""
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
. __SCRIPT__
$reason=$null
try { Assert-V6Interactive }
catch { $reason=$_.Exception.Message }
Write-Host ('RESULT=' + (@{error=$reason;stdin_is_redirected=[Console]::IsInputRedirected} | ConvertTo-Json -Compress))
""".replace("__SCRIPT__", ps_literal(SCRIPT))
    harness.write_text(source, encoding="utf-8-sig")
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [POWERSHELL, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(harness)],
        input="", text=True, encoding="utf-8", errors="replace",
        capture_output=True, env=env, timeout=45,
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    rendered = next(
        line[len("RESULT="):] for line in completed.stdout.splitlines()
        if line.startswith("RESULT=")
    )
    result = json.loads(rendered)
    assert result["stdin_is_redirected"] is True
    assert result["error"] == "V6_INTERACTIVE_CONSOLE_REQUIRED"

@pytest.mark.parametrize("prior_value", (None, "synthetic-parent-buffering-value"))
@pytest.mark.parametrize("child_exit", (0, 7))
def test_real_python_helper_unbuffered_child_and_parent_restore(
        tmp_path, prior_value, child_exit):
    """Read the actual child's env and keep only safe booleans in output."""
    root = tmp_path / "unbuffered child TEMP"
    root.mkdir()
    clone_temp_venv(root)
    child_path = root / "unbuffered_env_child.py"
    child_path.write_text(
        "import json, os, sys\n"
        "value = os.environ.get('PYTHONUNBUFFERED')\n"
        "print('UNBUFFERED_CHILD=' + json.dumps({"
        "'is_one': value == '1', 'exists': value is not None, "
        "'length': 0 if value is None else len(value)}))\n"
        f"sys.exit({child_exit})\n", encoding="utf-8"
    )
    sample = base64.b64encode(json.dumps({"prior": prior_value}).encode()).decode()
    source = r"""
. __SCRIPT__
$script:V6RepositoryRoot=__ROOT__
$sample=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__SAMPLE__')) | ConvertFrom-Json
$original=[Environment]::GetEnvironmentVariable('PYTHONUNBUFFERED','Process')
[Environment]::SetEnvironmentVariable('PYTHONUNBUFFERED',$sample.prior,'Process')
try {
    $values=@(Invoke-V6Python -Arguments @('-B',__CHILD__))
    $result=@{
        count=$values.Count;values=@($values)
        types=@($values | ForEach-Object {$_.GetType().FullName})
        parent_restored=([Environment]::GetEnvironmentVariable('PYTHONUNBUFFERED','Process') -ceq $sample.prior)
    }
    Write-Host ('RESULT=' + ($result | ConvertTo-Json -Depth 4 -Compress))
} finally {
    [Environment]::SetEnvironmentVariable('PYTHONUNBUFFERED',$original,'Process')
}
""".replace("__SCRIPT__", ps_literal(SCRIPT)).replace("__ROOT__", ps_literal(root)).replace("__SAMPLE__", sample).replace("__CHILD__", ps_literal(child_path))
    result, completed = run_ps(tmp_path, source)
    observed = json.loads(next(
        line[len("UNBUFFERED_CHILD="):] for line in completed.stdout.splitlines()
        if line.startswith("UNBUFFERED_CHILD=")
    ))
    assert observed["is_one"] is True, "Real nonCapture child must receive PYTHONUNBUFFERED=1"
    assert observed["exists"] is True and observed["length"] == 1
    assert result["count"] == 1 and result["values"] == [child_exit]
    assert result["types"] == ["System.Int32"]
    assert result["parent_restored"] is True


@pytest.mark.parametrize("prior_value", (None, "synthetic-parent-buffering-value"))
def test_real_python_helper_unbuffered_parent_restore_on_launch_exception(
        tmp_path, prior_value):
    """An actual executable-launch exception must also restore original env."""
    root = tmp_path / "failed child launch TEMP"
    python = root / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"TEMP deliberately invalid executable image")
    sample = base64.b64encode(json.dumps({"prior": prior_value}).encode()).decode()
    source = r"""
. __SCRIPT__
$script:V6RepositoryRoot=__ROOT__
$sample=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__SAMPLE__')) | ConvertFrom-Json
$original=[Environment]::GetEnvironmentVariable('PYTHONUNBUFFERED','Process')
[Environment]::SetEnvironmentVariable('PYTHONUNBUFFERED',$sample.prior,'Process')
try {
    $launchFailed=$false
    try { $null=Invoke-V6Python -Arguments @('-B') }
    catch { $launchFailed=$true }
    $result=@{
        launch_failed=$launchFailed
        parent_restored=([Environment]::GetEnvironmentVariable('PYTHONUNBUFFERED','Process') -ceq $sample.prior)
    }
    Write-Host ('RESULT=' + ($result | ConvertTo-Json -Depth 4 -Compress))
} finally {
    [Environment]::SetEnvironmentVariable('PYTHONUNBUFFERED',$original,'Process')
}
""".replace("__SCRIPT__", ps_literal(SCRIPT)).replace("__ROOT__", ps_literal(root)).replace("__SAMPLE__", sample)
    result, _ = run_ps(tmp_path, source)
    assert result["launch_failed"] is True
    assert result["parent_restored"] is True

def test_B29_tptimeout_fixed_g3t_uppercase_symbol_and_operator(tmp_path, runtime_repo):
    result, _ = launcher(tmp_path, runtime_repo, "tptimeout", "aapl")
    assert result["code"] == 0 and result["error"] is None
    assert result["calls"][0]["argv"] == [
        "-B", "-m", "v6lite", "test-tp-timeout", "AAPL", "--config",
        str(runtime_repo / "v6lite.g3t.toml"), "--operator", "John"]
    assert result["confirmations"] == result["interactive_checks"] == 1


@pytest.mark.parametrize("target", ("g3n", "g3t", "AAPL extra", "AAPL;", ""))
def test_B29_tptimeout_refuses_other_config_or_extra_target(tmp_path, runtime_repo, target):
    result, _ = launcher(tmp_path, runtime_repo, "tptimeout", target)
    assert result["error"] is not None and result["calls"] == []


def test_B29_tptimeout_whatif_is_plan_only(tmp_path, runtime_repo):
    result, completed = launcher(tmp_path, runtime_repo, "tptimeout", "AAPL", whatif=True)
    assert result["calls"] == [] and result["confirmations"] == 0
    plan = json.loads(next(line[5:] for line in completed.stdout.splitlines() if line.startswith("PLAN=")))
    assert plan["mode"] == "g3t" and "test-tp-timeout" in plan["argv"]
