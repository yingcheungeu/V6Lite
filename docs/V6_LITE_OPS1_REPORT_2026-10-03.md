# V6-Lite OPS-1：旧 V6 冻结、独立仓库与 venv

日期：2026-10-03 PT。依据：`V6_LITE_OPS1_REPO_FREEZE_VENV.md` 第 2 版。任务属于仓库维护与离线环境验证。本次没有连接 Gateway、运行交易引擎或探针，没有 push，没有改动旧 V6 Python、旧位置 `v6lite/`、`.env`、`state/` 或用户演练状态，也没有删除文件。所有写入由一个 Agent 串行完成；最终只读核验交由另一 Agent。

## 1. 只读预检

| 项 | 结果 |
|---|---|
| 进程 | V6-Lite 0、旧 V6 三个指定入口 0、无法判别命令行的 Python 0。满足第 1 项。 |
| 第十三批基线 | `v6lite/` 56 文件及运行手册 1 文件，57/57 SHA-256 匹配；`code_sha=d4b19f1d43b25448`。满足第 2 项。 |
| 旧仓库 | 原分支 `main`、原 HEAD `452a223b2e20f1b31db752f971ddfa03a3acfafb`。原 `git status --porcelain` 为已跟踪修改 137、删除 0、未跟踪状态项 115；把目录展开后未跟踪单文件 170。暂存 0。 |
| 旧 venv | `venv_v6` Python 3.11.9；`pip freeze` 为 49 个固定版本，清单见下。 |
| 任务计划 | 与 TradingV6 有关的任务 0；无明确启动旧 V6 的任务可禁用。 |
| 目标目录 | 预检时 `C:\Dev\V6Lite` 不存在。满足第 6 项。 |

旧 `venv_v6` 的 `pip freeze`（仅包名和版本）：

```text
aiohappyeyeballs==2.6.1
aiohttp==3.13.5
aiosignal==1.4.0
attrs==26.1.0
beautifulsoup4==4.14.3
certifi==2026.2.25
cffi==2.0.0
charset-normalizer==3.4.7
colorama==0.4.6
curl_cffi==0.15.0
discord.py==2.7.1
eventkit==1.0.3
frozendict==2.4.7
frozenlist==1.8.0
ib-insync==0.9.86
ibapi==9.81.1.post1
idna==3.11
iniconfig==2.3.0
markdown-it-py==4.0.0
mdurl==0.1.2
multidict==6.7.1
multitasking==0.0.12
nest-asyncio==1.6.0
numpy==2.4.4
packaging==26.1
pandas==3.0.2
peewee==4.0.4
platformdirs==4.9.6
pluggy==1.6.0
propcache==0.4.1
protobuf==7.34.1
psutil==7.2.2
pycparser==3.0
Pygments==2.20.0
pytest==9.0.3
pytest-asyncio==1.3.0
python-dateutil==2.9.0.post0
python-dotenv==1.2.2
pytz==2026.1.post1
requests==2.33.1
rich==15.0.0
six==1.17.0
soupsieve==2.8.3
typing_extensions==4.15.0
tzdata==2026.1
urllib3==2.6.3
websockets==16.0
yarl==1.23.0
yfinance==1.3.0
```

## 2. 旧 V6 冻结

- 分支：`legacy-v6-freeze-2026-10-03`，从上述原 HEAD 新建。
- 快照提交：`dc47d89481eebf976e1f8798daf0fe0de6520310`，消息 `Freeze legacy V6 working tree (2026-10-03)`。附注 tag `v6-frozen-2026-10-03` 指向该提交。
- A2 暂存 220 文件：已跟踪修改 137，获准的未跟踪源码/文档 83；目录分布：根目录 6、`docs` 12、`ops` 4、`scripts` 7、`tests` 114、`trading_bot_v6` 77。原有 `AGENTS.md` 修改作为已跟踪快照提交，OPS-1 未编辑其字节。
- 明确排除 `v6lite/`、`docs/V6_LITE_*`、根目录 `v6lite.*.toml` 运行配置、`state/`、`logs/`、`data/`、`evidence/`、`exports/`、`reports/`、`_temp*/`、`tradingv6_round2_acceptance/`、`venv*`、`ibc_windows/*.ini`、图片、根目录 `today_*.txt` 及 Git 忽略项；暂存排除路径违规 0。
- **A3 提交前硬门槛通过**：逐个扫描 220 个暂存 blob，超过 5 MiB 文件 0；实际 `.env` 敏感值、具体 Discord webhook URL、私钥头、Discord bot token 形态命中均为 0。宽泛 `token = "..."` 词法规则出现 3 处，均位于数字规范化测试，复核为可解析的 Decimal 数字样例，不是凭据。未输出候选原文。
- 启动入口 `start_v6.bat`、`start_trading.sh`、`watchdog.sh`、`setup_launchd.sh` 均只改名加 `.frozen`；前三个被 Git 跟踪的 shell 文件中实际为 `start_trading.sh`、`watchdog.sh`、`setup_launchd.sh`，在 Git 中保留原字节及执行位。`start_v6.bat` 原先未跟踪，改名后仍不入提交。
- 新增 `FROZEN_README.md` 说明冻结 tag、迁移位置及还原方法；第二提交 `8d246d56b05a114a502194f2c266949075782d4f`，消息 `Disable legacy launchers`。
- 与 TradingV6 有关的任务计划为 0，禁用任务 0；不确定任务 0。旧 Python 入口仍可被人工直接调用，因此周一仍须确认旧进程为 0。

## 3. 独立仓库与克隆

- `C:\Dev\V6Lite` 独立 Git 仓库，分支 `main`，继承旧仓库本地 Git 身份。`.gitattributes` 为 `* -text`，防止 checkout 改动字节；`.gitignore` 排除缓存、venv、SQLite、日志和根目录运行 TOML。
- 原 `v6lite/` 复制后 56 个包文件逐字节一致；原 `docs/V6_LITE_*` 复制 29 个文件。初次复制时第十三批清单 57/57 匹配，`code_sha=d4b19f1d43b25448`。后续仅按 B7 改新仓库运行手册，包文件仍保持第十三批基线。
- 初始提交 `d5c66e04483611524801cba5acf03e2a6ece0a56`，消息 `V6-Lite batch 13 (code_sha d4b19f1d43b25448)`；附注 tag `v6lite-batch13` 指向该提交。提交说明注明第 1–13 批的历史以各批报告的 SHA-256 清单为准。初始 `git ls-files` 为 87 文件；运行手册、两份依赖清单和本报告提交后预计为 90 文件，以最终独立核验为准。
- 全新本地克隆：`C:\Users\yu-ch\AppData\Local\Temp\v6lite_ops1_clone_8992eb95fd7843e0af5d5ee51116a1e3`。克隆中 56 个包文件复算 `code_sha=d4b19f1d43b25448`，`git ls-files` 为 87。克隆目录保留，未删除。
- 第 2 个新仓库提交使用 `Runbook for standalone repo and venv`，包含运行手册、依赖清单和本报告；本报告位于该提交内，故不在自身内容中写它的哈希，最终核验及交付摘要给出实际 HEAD。

## 4. 独立 venv 与离线验证

使用与旧 venv 相同的 Python 3.11.9 建立 `C:\Dev\V6Lite\.venv`；旧 venv 未改。扫描 `v6lite/` imports 后，基础环境仅安装其运行/测试依赖闭包，版本均与旧 venv 一致。新 venv 的 `pip freeze` 写入 `requirements.txt`，共 14 项：

```text
colorama==0.4.6
eventkit==1.0.3
ib-insync==0.9.86
iniconfig==2.3.0
nest-asyncio==1.6.0
numpy==2.4.4
packaging==26.1
pluggy==1.6.0
psutil==7.2.2
Pygments==2.20.0
pytest==9.0.3
pytest-asyncio==1.3.0
typing_extensions==4.15.0
tzdata==2026.1
```

`g2_probe --compare-yfinance` 的 `yfinance` 是可选 import；额外 24 个固定版本依赖（含 `yfinance==1.3.0`）另列于 `requirements-probe.txt`，以 `-r requirements.txt` 引用基础依赖。可选组未安装到周一 G2-D 的基础 venv；如要对比 yfinance，先执行 `python -m pip install -r .\requirements-probe.txt`。

Windows 新 venv 全量命令：设置 `V6LITE_LEGACY_SCANNER=C:\Dev\TradingV6\market_scanner.py`、UTF-8 和 TEMP pycache 后，串行执行 `python -m pytest v6lite/tests -q -p no:cacheprovider` 三次：

| 次数 | JUnit | 结果 | 时长 |
|---|---|---|---|
| 1 | `%TEMP%\v6lite_ops1_full1.xml` | 443 passed，0 failed/errors/skipped | 51.811 s |
| 2 | `%TEMP%\v6lite_ops1_full2.xml` | 443 passed，0 failed/errors/skipped | 49.911 s |
| 3 | `%TEMP%\v6lite_ops1_full3.xml` | 443 passed，0 failed/errors/skipped | 49.586 s |

`python -m v6lite --help` 退出码 0；`ib_insync.__version__=0.9.86`。没有运行 `v6lite run` 或 `g2_probe`，也没有连接 Gateway。

## 5. 配置差异与运行手册

只比较和列出允许公开的配置键，账户标识及告警地址/凭据均未输出：

```diff
# v6lite.paper.toml: 原位置运行配置与 v6lite/examples/paper.toml 的各项值一致
- paths.foreign_lock_paths = ["state/trading_bot_v6.lock", "trading_bot.lock"]
+ paths.foreign_lock_paths = ["C:/Dev/TradingV6/state/trading_bot_v6.lock", "C:/Dev/TradingV6/trading_bot.lock"]

# v6lite.paper.delayed.toml: 由 v6lite/examples/paper_delayed.toml 生成
- paths.foreign_lock_paths = ["state/trading_bot_v6.lock", "trading_bot.lock"]
+ paths.foreign_lock_paths = ["C:/Dev/TradingV6/state/trading_bot_v6.lock", "C:/Dev/TradingV6/trading_bot.lock"]
```

两份配置各自的 `state_dir` 均未改；Paper 使用原样默认目录，G2-D 使用样例的独立 `~/.v6lite/paper-delayed`。Paper 市场数据类型 1，G2-D 类型 3；除此两项及外部锁路径外，两份目标 TOML 的结构和值一致（包括账户和告警环境变量名，具体值隐去）。两份根目录 TOML 均被 `.gitignore` 命中，不入 Git。

新运行手册改为 `C:\Dev\V6Lite` 路径和 `.\.venv\Scripts\Activate.ps1`；说明旧位置只作回退、新配置已经就位，以及每批经 Claude 复核、John 同意后才 commit/tag、交易日之间切换、不 push 的流程。第十三批运行代码包仍为 `code_sha=d4b19f1d43b25448`；运行手册自身按 B7 预期不同于第十三批原始清单。

## 6. John 周一从新位置启动 G2-D

**先人工确认**旧 V6 与旧位置 V6-Lite 均已停止，IB Gateway 登录 Paper 且 Read-Only API 已勾选。新开 PowerShell；不要把 webhook URL 回显或放在命令行。OPS-1 当前执行 shell 未见该变量，不代表 John 的交互用户环境没有；下面从 User 环境载入并仅显示存在/长度：

```powershell
cd C:\Dev\V6Lite
. .\.venv\Scripts\Activate.ps1
$env:PYTHONUTF8='1'
$wh = [Environment]::GetEnvironmentVariable('V6LITE_DISCORD_WEBHOOK', 'User')
if ([string]::IsNullOrWhiteSpace($wh)) { throw 'V6LITE_DISCORD_WEBHOOK is missing in User environment' }
$env:V6LITE_DISCORD_WEBHOOK = $wh
"webhook_present=True length=$($wh.Length)"
python -c "from v6lite.version import code_sha; s=code_sha(); print('code_sha='+s); assert s=='d4b19f1d43b25448'"
Get-FileHash .\v6lite\engine.py -Algorithm SHA256
python -m v6lite run --config .\v6lite.paper.delayed.toml --dry-run
```

若 G2-D 状态**尚未创建**，首次启动完成后在该窗口按 `Ctrl+C`，再执行下列标记和重启；若已有演练状态，先检查其 ignore 记录，避免重复修改：

```powershell
python -m v6lite ignore ABT --dry-run --config .\v6lite.paper.delayed.toml --operator John
python -m v6lite ignore IBB --dry-run --config .\v6lite.paper.delayed.toml --operator John
python -m v6lite ignore IWM --dry-run --config .\v6lite.paper.delayed.toml --operator John
python -m v6lite ignore PANW --dry-run --config .\v6lite.paper.delayed.toml --operator John
python -m v6lite run --config .\v6lite.paper.delayed.toml --dry-run
```

启动后核对手机中文启动通知、`code_sha`、`data_mode: DELAYED rehearsal only`、旧 V6 状态及零 broker 写调用；异常按运行手册停止并留证。G2-D 不计入实时行情 G2 有效日，也不放行 G3/LIVE。**若周一 06:00 PT 前 OPS-1 未完成且未获 Claude 复核，仍从 `C:\Dev\TradingV6` 用 `venv_v6` 运行第十三批。**