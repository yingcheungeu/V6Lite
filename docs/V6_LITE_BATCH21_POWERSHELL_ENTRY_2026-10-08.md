# 第21批：PowerShell直接入口与Ctrl+C停机验证

日期：2026-10-08（PT）。范围：只在 `C:\Dev\V6Lite-dev` 的 dev 修改运维脚本和测试；交易包 `code_sha=cfe16edd7a7e4d7f` 不变。第20批已提交/tag，第21批尚未提交。运行仓库、John配置、用户启动入口、PowerShell profile和 `%USERPROFILE%\.v6lite` 均未修改。

## 1. 发现 → 改动 → 测试

### 发现

John报告10/8经 `v6.cmd` 启动后按Ctrl+C出现 `Terminate batch job (Y/N)?`，选Y后没有SHUTDOWN并留下wal/shm。本轮没有重读该运行目录。这个事件说明旧cmd入口不能作为正确停机入口。

独立TEMP控制台进一步证明：**仅去掉cmd仍不够**。第20批 `Invoke-V6Python` 的 `python | Out-Host` 原生管道，在真实CTRL_C_EVENT下使Python结束在SIGNAL之后、SHUTDOWN之前，父PowerShell已经回到prompt。证据：`old_out_host_red_events.json`只有READY和一次SIGNAL，`old_out_host_red_prompt_events.jsonl`显示python_alive=false，`old_out_host_red_signal.json`记录信号只发送到本测试持有的控制台。

### 改动

1. 新增一行用户入口模板 `scripts/v6-user.ps1`：

   ```powershell
   & 'C:\Dev\V6Lite\scripts\v6.ps1' @args
   ```

   它直接在已有PowerShell里调用脚本，无cmd、无批处理Y/N提示。模板没有安装到用户目录；旧用户v6.cmd保留原样。
2. `scripts/v6.ps1` 的非Capture运行分支改用 `System.Diagnostics.Process`、UseShellExecute=false，直接继承控制台与stdin/stdout/stderr。使用Windows CRT argv转义，不把参数解释为shell代码，空参数、路径、引号和尾部反斜杠均保留。
3. 父脚本在正常路径和finally都等待这一个自建Python完成，再释放Process并恢复PYTHONUNBUFFERED。保留PYTHONUNBUFFERED=1、退出码和原有环境恢复。不通过Out-Host或PowerShell原生管道启动长期运行Python，也不主动终止它。
4. Capture配置/status分支、Invoke-V6Launcher全部账户/端口/client_id/YES确认/tag/配置指纹/复核门控原样保留。第21批没有改引擎signal handler、撤单、保护、audit或交易路径。

Windows CTRL_C_EVENT发送给同一控制台的进程；控制台附着与信号语义见 [Microsoft GenerateConsoleCtrlEvent](https://learn.microsoft.com/en-us/windows/console/generateconsolectrlevent) 和 [AttachConsole](https://learn.microsoft.com/en-us/windows/console/attachconsole)。测试在发信号前核验控制台PID及Toolhelp父子归属；不向John终端、Codex终端或其他交易进程广播。

### 测试

| 证据/阶段 | 结果 | 含义 |
|---|---|---|
| 第20批新增入口测试 | 10 failed | 旧代码没有直接入口，参数合同自然失败；不替代下行产品行为红证据。 |
| 第20批Out-Host真实Ctrl+C | 失败：SIGNAL后没有SHUTDOWN | 旧原生管道不能等待正常停机；证据保持原样。 |
| 首版argv转义候选 | 2 failed / 2 passed | 带引号/反斜杠参数错误；修正转义后通过，未隐藏此失败。 |
| 合成取消夹具修正 | 最初未模拟撤单确认，unresolved断言失败 | FakeBroker.cancel只记录请求；补充模拟transport ACK和时钟推进，保留DAY取消、GTC保留、无unresolved全部强断言。这不是产品测试偶发重试。 |
| 新入口/argv聚焦 | 14 passed | 一行入口、9组参数及父退出码、4组native argv。 |
| 独立真实Ctrl+C演练 | 4/4 PASS | Windows PowerShell 5.1 / PowerShell 7 × dry / paper_fake。 |
| 最终串行scripts全量 | **130 passed / 0 failed / 0 errors / 0 skipped，164.24秒** | 112项旧运维回归+18项新测试（其中包含4项Ctrl+C），不是额外相加。 |

命令（已执行，2026-10-08 20:50 PT开始）：

```powershell
. C:\Dev\V6Lite-dev\.venv\Scripts\Activate.ps1
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONUTF8='1'
$env:PYTHONUNBUFFERED='1'
python -u -B -m pytest scripts/tests -q -p no:cacheprovider --tb=short --junitxml docs/batch21_evidence/scripts_final.xml
```

本轮交易包没改，未重跑交易全量；第20批冻结的Windows默认全量连续3次1055 passed仍在其报告，不能把它们算作第21批新的全量结果。

## 2. 合成控制台实测方法与边界

`console_shutdown_probe.py` 创建属于测试的隐藏CREATE_NEW_CONSOLE PowerShell，调用TEMP的一行入口→mock endpoint→实际Invoke-V6Python；child只创建FakeBroker/Rig、MemoryChannel、TEMP数据库和TEMP AccountLock，调用现有run_engine/Engine.shutdown。没有调用真实 `v6lite run`、没有创建IBKR Broker、没有外发通知、没有访问用户状态。

helper附着到该测试PowerShell控制台，核对成员PID均为其后代或本helper，发送**一次CTRL_C_EVENT**。child在shutdown前人为等待1.5秒，以便发现父脚本提前返回；此延迟不写入生产代码。非dry夹具另建未成交DAY买单和已确认20股GTC保护，并模拟券商撤单ACK。

四组结果都按顺序记录：

`SIGNAL(1) → SHUTDOWN_BEGIN → SHUTDOWN_AUDIT_WRITTEN → DISCONNECT_DONE → audit核对 → ALERTER_CLOSED → STORE_CLOSED → LOCK_RELEASED → CHILD_FINAL → parent prompt(python_alive=false)`。

非dry两组还确认DAY单取消、20股GTC保留，SHUTDOWN的unresolved=[]、broker_working=[]；dry两组place/cancel均为0。父PowerShell仍活着、cwd未变、父PYTHONUNBUFFERED恢复。只有子进程结束后才结束测试自建的NoExit PowerShell。

独立四组证据为 `console_shutdown_results.json`，最终pytest XML也包含四项。该方法证明被拥有的ConsoleHost和Python停机合同；**不等同于John实际终端按键、完整真实CLI资源生命周期或TWS订单路径验收**。完整启动门控由112项既有scripts测试回归，信号探针直接测试底层Python运行分支，不调用完整真实Invoke-V6Launcher。

## 3. 部署顺序：本轮尚未部署

运行main仍是旧脚本，不能只复制一行入口就把旧Out-Host变安全。必须先经Claude复核、另行授权把本批运行脚本部署到运行仓库，再由John将模板复制/新建为 `%USERPROFILE%\v6.ps1`。本轮未做这两步，不修改旧cmd，也没有安装函数/profile。

部署之后，从已有PowerShell使用完整.ps1路径，避免命令搜索仍命中v6.cmd：

```powershell
& "$env:USERPROFILE\v6.ps1" g3t-dry
# 只在获得相应运行批准之后：
& "$env:USERPROFILE\v6.ps1" g3t
& "$env:USERPROFILE\v6.ps1" status g3t
```

如当前执行策略阻止脚本，可在新开的PowerShell进程使用 `-ExecutionPolicy Bypass`；不要因此永久修改系统策略。调用/作用域规则见 [Microsoft about_Scripts](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_scripts?view=powershell-5.1)。仍必须通过原有运行tag、干净交易包、账户、端口7497、client132、3股、保护、User webhook和真实模式YES确认门控。

## 4. John实际键盘验收步骤（本轮只写步骤，未执行）

1. 在已获批准的测试窗口、已部署新版脚本之后，从已有PowerShell完整.ps1路径启动获准的模式。先做dry；真实Paper验证另需相应授权。记录启动HEAD/tag/code_sha、模式、Python PID和UTC时间；不记录URL/token。
2. **只按一次Ctrl+C**。应无批处理Y/N提示。不要再按Ctrl+C、关闭窗口、结束进程或选Y来代替等待。若还出现Y/N，说明仍走cmd入口，此轮不能算通过。
3. 等Python自行退出、PowerShell提示符重新出现。确认该Python PID已结束。未回到提示符时不能声称停机完成；若超过正常宽限，保留现状供排查，禁止以强制结束作为验收通过。
4. SHUTDOWN是当前代码的SQLite **audit事件**，不是保证存在的一行console日志。由John在停止后用只读SQLite工具对实际数据库执行：

   ```sql
   SELECT ts, kind, payload FROM audit
   WHERE kind='SHUTDOWN' ORDER BY id DESC LIMIT 1;
   ```

   工具必须以SQLite URI `mode=ro` 打开。ts应对应本次停止；dry的payload包含dry_run=true；非dry应核对unresolved及broker_working为空（该列表不包含保留的保护GTC）。John另在TWS核对DAY单终态、应保留的GTC数量/身份。若报告unresolved或保护不明，按现有处置流程处理，不能把“进程已退出”当作券商订单已安全。
5. audit是在disconnect之前写入，仅有SHUTDOWN记录不足以证明后续alerter/store/lock完整退出；必须联合第3步退出证据及订单核对。wal/shm存在本身也不证明失败；不要手工删除它们。

本轮不添加假的生产“SHUTDOWN complete”日志；合成child的 `SYNTHETIC_SHUTDOWN_COMPLETE` 仅为测试证据。

## 5. 历史状态与交付

10/8 AAPL历史不做身份恢复或迁移。John手动确认0股、无在途订单后自行把paper-test整体改名留证，下次G3-T从新状态开始。Codex不读取/枚举/修改该目录，也不声称归档已完成。

交付SHA-256清单见 `docs/batch21_evidence/sha256_manifest.json`；自引用避免方式：清单不包括自身，报告哈希在交付消息及独立复核记录中列出。第21批不commit/tag，不合并main、不push、不连接TWS/Gateway、不修改保护参数。是否继续G3-T由Claude/John裁定。
