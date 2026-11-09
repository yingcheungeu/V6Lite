# Operational launcher: batch 18 gates, batch 21 direct-console shutdown wait.
# Does not modify configuration or trading code.
[CmdletBinding(SupportsShouldProcess=$true)]
param(
    [Parameter(Position=0)][string]$Mode,
    [Parameter(Position=1)][string]$Target,
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$Remaining
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:V6RepositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$script:V6Modes = @('dry', 'g3t-dry', 'g3t', 'g3n-dry', 'g3n')

function Stop-V6([string]$Reason) { throw [InvalidOperationException]::new('V6_' + $Reason) }
function Get-V6RepositoryRoot { return $script:V6RepositoryRoot }
function Get-V6UserWebhook { return [Environment]::GetEnvironmentVariable('V6LITE_DISCORD_WEBHOOK', 'User') }
function Assert-V6Interactive {
    if (-not [Environment]::UserInteractive -or [Console]::IsInputRedirected -or $Host.Name -ne 'ConsoleHost') {
        Stop-V6 'INTERACTIVE_CONSOLE_REQUIRED'
    }
}
function Read-V6Confirmation { return (Read-Host 'Paper order/state write allowed. Type the full word YES to continue') }
function Invoke-V6Git([string[]]$Arguments) {
    $output = @(& git --no-optional-locks -C (Get-V6RepositoryRoot) @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) { Stop-V6 'GIT_CHECK_FAILED' }
    return (($output | ForEach-Object { [string]$_ }) -join [Environment]::NewLine).Trim()
}
function Get-V6GitIdentity {
    $head = Invoke-V6Git @('rev-parse', 'HEAD')
    if ($head -notmatch '^[0-9a-f]{40}$') { Stop-V6 'INVALID_HEAD' }
    if ((Invoke-V6Git @('branch', '--show-current')) -cne 'main') { Stop-V6 'RUNTIME_MAIN_REQUIRED' }
    if (Invoke-V6Git @('status', '--porcelain', '--untracked-files=all', '--', 'v6lite')) { Stop-V6 'PACKAGE_DIRTY' }
    $names = Invoke-V6Git @('tag', '--points-at', $head, '--list', 'v6lite-run-*')
    $valid = @()
    foreach ($name in ($names -split '[\r\n]+')) {
        if ($name -and (Invoke-V6Git @('cat-file', '-t', ('refs/tags/' + $name))) -ceq 'tag') {
            $peeled = Invoke-V6Git @('rev-parse', ($name + '^{commit}'))
            if ($peeled -ceq $head) { $valid += $name }
        }
    }
    if ($valid.Count -eq 0) { Stop-V6 'ANNOTATED_RUN_TAG_REQUIRED' }
    return [pscustomobject]@{ Head=$head; Tag=($valid | Sort-Object | Select-Object -Last 1) }
}

# This program only validates configuration / computes source evidence, or
# SELECTs the explicitly selected SQLite database using mode=ro. Never Store.
$script:V6ReadOnlyProgram = @'
import hashlib, json, sqlite3, subprocess, sys
from pathlib import Path
from datetime import datetime, timezone
try:
    from v6lite.config import load_config
    from v6lite.version import code_sha
    from v6lite.store import SCHEMA_VERSION
    from v6lite.models import ACTIVE_STATUSES
    operation, config_path, dry_text = sys.argv[1:4]
    cfg = load_config(config_path)
    root = Path.cwd()
    def committed_sha():
        tree = subprocess.check_output(['git','--no-optional-locks','ls-tree','-r','-z','HEAD','--','v6lite'])
        entries=[]
        for record in tree.split(b'\0'):
            if not record: continue
            info,name=record.split(b'\t',1); mode,kind,oid=info.split()
            path=name.decode('utf-8')
            if '__pycache__' in Path(path).parts: continue
            if kind!=b'blob' or mode not in (b'100644',b'100755'): raise ValueError('invalid package tree')
            entries.append((path[len('v6lite/'):],oid))
        entries.sort()
        batch=subprocess.run(['git','--no-optional-locks','cat-file','--batch'],input=b''.join(oid+b'\n' for _,oid in entries),stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True).stdout
        cursor=0;digest=hashlib.sha256()
        for relative,oid in entries:
            end=batch.index(b'\n',cursor); fields=batch[cursor:end].split(); size=int(fields[2]);cursor=end+1
            if fields[0]!=oid or fields[1]!=b'blob': raise ValueError('invalid blob')
            data=batch[cursor:cursor+size];cursor+=size+1
            digest.update(relative.encode());digest.update(b'\0');digest.update(data);digest.update(b'\0')
        return digest.hexdigest()[:16]
    if operation=='probe':
        print(json.dumps(dict(mode=cfg.mode, paper_account=cfg.account.startswith('DU'), host=cfg.host,
            port=cfg.port,client_id=cfg.client_id,test_enabled=cfg.test.enabled,test_qty=cfg.test.qty,
            protection_enabled=cfg.protection.enabled,market_data_type=cfg.market_data_type,
            webhook_env=cfg.alerts.discord_webhook_env,tradable=list(cfg.tradable_symbols),
            code_sha=code_sha(),tag_code_sha=committed_sha(),
            config_sha256=hashlib.sha256(Path(config_path).read_bytes()).hexdigest())))
    elif operation=='status':
        dry=dry_text=='true'
        base=cfg.resolved_state_dir();path=((base/'dryrun' if dry else base)/'v6lite.sqlite3').absolute()
        expected_role='dryrun' if dry else ('test' if cfg.test.enabled else 'formal')
        out=dict(mode=cfg.mode,role=expected_role,read_only=True,
            config_fingerprint=cfg.fingerprint()[:16],client_id=cfg.client_id,
            api_client_id=cfg.client_id+1000 if dry else cfg.client_id)
        if not path.is_file():
            out['state']='NO_STATE'
        else:
            with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2.0) as conn:
                conn.execute('PRAGMA query_only=ON')
                meta=dict(conn.execute('SELECT key,value FROM meta'))
                if (meta.get('account'),meta.get('mode'),meta.get('role'),meta.get('schema_version'),meta.get('client_id')) != (cfg.account,cfg.mode,expected_role,SCHEMA_VERSION,str(cfg.client_id)):
                    raise ValueError('state identity mismatch')
                out['state']='OPENED_RO'
                out['open_positions']=[dict(symbol=s,qty_open=q,stop=stop) for s,q,stop in conn.execute("SELECT symbol,qty_open,stop FROM positions WHERE status='OPEN'")]
                active=tuple(str(x) for x in ACTIVE_STATUSES)
                marks=",".join("?" for _ in active)
                out['active_order_count']=conn.execute("SELECT COUNT(*) FROM intents WHERE status IN ("+marks+")",active).fetchone()[0]
                controls=dict(conn.execute("SELECT key,value FROM controls WHERE key IN ('health','kill','cooldown_until','loss_streak')"))
                out['kill']=bool(controls.get('kill'))
                out['loss_streak']=int(controls['loss_streak']) if controls.get('loss_streak') else 0
                out['cooldown_until']=datetime.fromisoformat(controls['cooldown_until']).isoformat() if controls.get('cooldown_until') else None
                out['anomaly_count']=conn.execute("SELECT COUNT(*) FROM controls WHERE key LIKE 'anomaly:%'").fetchone()[0]
                out['engine_health']='unknown (engine has not reported)'
                if controls.get('health'):
                    health=json.loads(controls['health'])
                    at=datetime.fromisoformat(health['at'])
                    age=(datetime.now(timezone.utc)-at).total_seconds()
                    out['engine_health']={k:bool(health[k]) for k in ('broker_connected','reconcile_ok','entries_allowed') if k in health}
                    out['engine_health'].update(at=at.isoformat(),age_seconds=round(age),freshness='fresh' if 0<=age<=180 else 'STALE',entry_block_count=len(health.get('entry_blocks',[])))
        print(json.dumps(out,ensure_ascii=True,indent=2))
    else:
        raise ValueError('unsupported operation')
except Exception:
    print(json.dumps({'error':'READ_ONLY_CHECK_FAILED'}))
    sys.exit(2)
'@

function ConvertTo-V6WindowsArgument([string]$Value) {
    # Windows CRT argv rules: double backslashes before quotes and at the end
    # of a quoted argument. ProcessStartInfo invokes no shell.
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '\\+$', '$0$0')
    return '"' + $escaped + '"'
}

function Invoke-V6Python {
    param([string[]]$Arguments, [switch]$Capture, [string]$InputCode)
    $python = Join-Path (Get-V6RepositoryRoot) '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { Stop-V6 'VENV_PYTHON_MISSING' }
    if ($Capture) {
        if ($InputCode) { $output = @($InputCode | & $python @Arguments 2>&1) }
        else { $output = @(& $python @Arguments 2>&1) }
        $code = $LASTEXITCODE
        return [pscustomobject]@{ ExitCode=$code; Output=(($output | ForEach-Object { [string]$_ }) -join [Environment]::NewLine) }
    }
    $oldUnbuffered = [Environment]::GetEnvironmentVariable('PYTHONUNBUFFERED', 'Process')
    $process = [Diagnostics.Process]::new()
    $started = $false
    try {
        [Environment]::SetEnvironmentVariable('PYTHONUNBUFFERED', '1', 'Process')
        # Share the current console directly. A PowerShell native pipeline can
        # terminate Python on Ctrl+C before its signal handler finishes shutdown.
        $process.StartInfo.FileName = $python
        $process.StartInfo.UseShellExecute = $false
        $process.StartInfo.WorkingDirectory = Get-V6RepositoryRoot
        $quoted = @($Arguments | ForEach-Object { ConvertTo-V6WindowsArgument $_ })
        $process.StartInfo.Arguments = [string]::Join(' ', [string[]]$quoted)
        $started = $process.Start()
        if (-not $started) { Stop-V6 'PYTHON_START_FAILED' }
        $process.WaitForExit()
        return [int]$process.ExitCode
    } finally {
        # Even a cancelled parent pipeline must wait for the owned child. Do
        # not kill it or restore its environment while cleanup is still running.
        try { if ($started) { $process.WaitForExit() } }
        finally {
            $process.Dispose()
            [Environment]::SetEnvironmentVariable('PYTHONUNBUFFERED', $oldUnbuffered, 'Process')
        }
    }
}
function Get-V6Configuration([string]$ConfigPath) {
    $result = Invoke-V6Python -Arguments @('-B','-','probe',$ConfigPath,'false') -InputCode $script:V6ReadOnlyProgram -Capture
    if ($result.ExitCode -ne 0) { Stop-V6 'CONFIG_CHECK_FAILED' }
    try { return ($result.Output | ConvertFrom-Json) } catch { Stop-V6 'CONFIG_CHECK_FAILED' }
}
function Assert-V6Configuration($Config, [string]$SelectedMode, [string]$Symbol) {
    if ($Config.mode -cne 'paper' -or -not $Config.paper_account -or $Config.host -notin @('127.0.0.1','localhost','::1')) { Stop-V6 'PAPER_LOCALHOST_REQUIRED' }
    if ($Config.webhook_env -cne 'V6LITE_DISCORD_WEBHOOK') { Stop-V6 'USER_WEBHOOK_KEY_REQUIRED' }
    if ($Config.code_sha -notmatch '^[0-9a-f]{16}$' -or $Config.code_sha -cne $Config.tag_code_sha) { Stop-V6 'PACKAGE_HASH_MISMATCH' }
    if ($SelectedMode -like 'g3t*') {
        if (-not $Config.test_enabled -or $Config.client_id -ne 132 -or $Config.test_qty -ne 3) { Stop-V6 'G3T_IDENTITY_REQUIRED' }
    } elseif ($Config.test_enabled -or $Config.client_id -ne 131) { Stop-V6 'G3N_IDENTITY_REQUIRED' }
    if ($SelectedMode -like 'g3*') {
        if ($Config.port -ne 7497) { Stop-V6 'TWS_PAPER_7497_REQUIRED' }
        if (-not $Config.protection_enabled -or $Config.market_data_type -ne 1) { Stop-V6 'G3_PROTECTION_REALTIME_REQUIRED' }
    } elseif ($Config.port -notin @(4002,7497)) { Stop-V6 'PAPER_PORT_REQUIRED' }
    if ($Symbol -and $Symbol -cnotin @($Config.tradable)) { Stop-V6 'ENTRY_SYMBOL_NOT_TRADABLE' }
}

function Invoke-V6Launcher {
    [CmdletBinding(SupportsShouldProcess=$true)]
    param([string]$Action, [string]$Target, [string[]]$Unexpected)
    if ($null -ne $Unexpected -and $Unexpected.Count -gt 0) { Stop-V6 'EXTRA_ARGUMENTS_REFUSED' }
    $symbol = $null
    if ($Action -ceq 'status') {
        $selected = if ($Target) { $Target } else { 'dry' }
        if ($selected -cnotin $script:V6Modes) { Stop-V6 'STATUS_MODE_INVALID' }
    } elseif ($Action -cin @('entry','tptimeout')) {
        if (-not $Target -or $Target -notmatch '^[A-Za-z][A-Za-z0-9.]{0,9}$') { Stop-V6 'ENTRY_SYMBOL_INVALID' }
        $symbol = $Target.ToUpperInvariant();$selected='g3t'
    } elseif ($Action -cin $script:V6Modes) {
        if ($Target) { Stop-V6 'EXTRA_ARGUMENTS_REFUSED' }
        $selected=$Action
    } else { Stop-V6 'USAGE_V6_MODE_OR_ENTRY_OR_STATUS' }
    $dry = $selected -ceq 'dry' -or $selected.EndsWith('-dry',[StringComparison]::Ordinal)
    $configName = if ($selected -like 'g3t*') { 'v6lite.g3t.toml' } elseif ($selected -like 'g3n*') { 'v6lite.g3n.toml' } else { 'v6lite.paper.toml' }
    $root=Get-V6RepositoryRoot
    $configPath=Join-Path $root $configName
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { Stop-V6 'CONFIG_MISSING' }
    if (-not (Test-Path -LiteralPath (Join-Path $root '.venv\Scripts\python.exe') -PathType Leaf)) { Stop-V6 'VENV_PYTHON_MISSING' }
    Push-Location -LiteralPath $root
    try {
        $identity=Get-V6GitIdentity
        $cfg=Get-V6Configuration $configPath
        Assert-V6Configuration $cfg $selected $symbol
        Write-Host ('HEAD=' + $identity.Head)
        Write-Host ('tag=' + $identity.Tag)
        Write-Host ('code_sha=' + $cfg.code_sha)
        $webhook=Get-V6UserWebhook
        $exists=-not [string]::IsNullOrWhiteSpace($webhook)
        $length=if ($null -eq $webhook) { 0 } else { $webhook.Length }
        Write-Host ('Webhook User exists={0} length={1}' -f $exists,$length)
        if ($Action -cin @('entry','tptimeout')) {
            $command=if ($Action -ceq 'entry') { 'test-entry' } else { 'test-tp-timeout' }
            $arguments=@('-B','-m','v6lite',$command,$symbol,'--config',$configPath,'--operator','John')
        } else {
            $arguments=@('-B','-m','v6lite','run','--config',$configPath)
            if ($dry) { $arguments+='--dry-run' }
        }
        if ($Action -ceq 'status') {
            $dryText=if ($dry) { 'true' } else { 'false' }
            $arguments=@('-B','-','status',$configPath,$dryText)
            $plan=@{action='status';mode=$selected;config=$configName;read_only=$true;dry_run_state=$dry}
        } else { $plan=@{action=$Action;mode=$selected;config=$configName;argv=$arguments} }
        if ($WhatIfPreference) {
            Write-Host ('PLAN=' + ($plan | ConvertTo-Json -Depth 4 -Compress))
            return 0
        }
        if ($Action -cne 'status' -and -not $exists) { Stop-V6 'USER_WEBHOOK_MISSING' }
        if ($Action -cne 'status' -and -not $dry) {
            Assert-V6Interactive
            $answer=Read-V6Confirmation
            if (-not [string]::Equals($answer,'YES',[StringComparison]::Ordinal)) { Stop-V6 'YES_CONFIRMATION_REQUIRED' }
        }
        # Recheck mutable evidence after any interactive pause; never reuse an
        # approved plan after HEAD, package or config changes.
        $lastIdentity=Get-V6GitIdentity
        $lastCfg=Get-V6Configuration $configPath
        Assert-V6Configuration $lastCfg $selected $symbol
        if ($lastIdentity.Head -cne $identity.Head -or $lastIdentity.Tag -cne $identity.Tag -or $lastCfg.config_sha256 -cne $cfg.config_sha256) { Stop-V6 'EVIDENCE_CHANGED' }
        $oldWebhook=[Environment]::GetEnvironmentVariable('V6LITE_DISCORD_WEBHOOK','Process')
        $oldBytecode=[Environment]::GetEnvironmentVariable('PYTHONDONTWRITEBYTECODE','Process')
        try {
            [Environment]::SetEnvironmentVariable('V6LITE_DISCORD_WEBHOOK',$webhook,'Process')
            [Environment]::SetEnvironmentVariable('PYTHONDONTWRITEBYTECODE','1','Process')
            if ($Action -ceq 'status') {
                $result=Invoke-V6Python -Arguments $arguments -InputCode $script:V6ReadOnlyProgram -Capture
                if ($result.ExitCode -ne 0) { Stop-V6 'READ_ONLY_STATUS_REFUSED' }
                try { $null=$result.Output | ConvertFrom-Json } catch { Stop-V6 'READ_ONLY_STATUS_REFUSED' }
                Write-Host $result.Output
                return 0
            }
            return (Invoke-V6Python -Arguments $arguments)
        } finally {
            [Environment]::SetEnvironmentVariable('V6LITE_DISCORD_WEBHOOK',$oldWebhook,'Process')
            [Environment]::SetEnvironmentVariable('PYTHONDONTWRITEBYTECODE',$oldBytecode,'Process')
        }
    } finally { Pop-Location }
}

# Dot-source supports independent tests with ordinary PowerShell function
# replacements; actual command execution has no bypass or alternate config.
if ($MyInvocation.InvocationName -ne '.') {
    try { exit (Invoke-V6Launcher -Action $Mode -Target $Target -Unexpected $Remaining -WhatIf:$WhatIfPreference) }
    catch {
        $reason=$_.Exception.Message
        if ($reason -notmatch '^V6_[A-Z0-9_]+$') { $reason='V6_CHECK_FAILED' }
        [Console]::Error.WriteLine('V6 refused: ' + $reason)
        exit 2
    }
}
