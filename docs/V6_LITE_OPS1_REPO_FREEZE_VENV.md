# OPS-1 指令（Claude → Codex）：V6-Lite 独立仓库与 venv、冻结旧 V6

日期：2026-10-03。**第 2 版**：迁移基线由第十二批改为第十三批。第 1 版在预检第 2 项按规定停止，因为当时工作区已经是第十三批代码。第十三批已经由 Claude 复核通过：57 项哈希一致，`code_sha` 复算为 `d4b19f1d43b25448`，沙盒中 442 passed、1 skipped。

John 已决定三件事：① 给 V6-Lite 建 git 记录；② 冻结旧 V6；③ 给 V6-Lite 单独建 venv。本指令授权 Codex 执行下面列出的 git 和文件操作；**除此之外的写操作一律不做**。

分工：调查和核对可以并行；**所有写操作由一个 Agent 串行执行**；最终验证由另一个 Agent 独立完成。

## 0 总体方案（为什么这样做）

- **V6-Lite 迁到独立目录 `C:\Dev\V6Lite`，建独立的 git 仓库和 `.venv`。** 不在旧仓库里开分支：`v6lite\` 如果只在某个分支上被跟踪，一切回旧分支，它就会从工作区消失，正在使用的演练代码也会跟着消失。独立目录也回答了 John 之前的疑问：新旧两套系统的代码确实放在同一个目录里。
- **旧 V6 在原仓库里冻结**：在新分支上提交当前工作区的快照，打 tag，并停用启动脚本。旧 V6 的 python 代码一律不改。
- **代码逐字节原样复制，不做任何修改**：新位置算出的 `code_sha` 必须仍为 `d4b19f1d43b25448`（第十三批）。周一 G2-D 就用这个版本，和第十三批报告的清单一一对应。
- **旧位置 `C:\Dev\TradingV6\v6lite\` 暂时保留不动，作为回退**：等 John 确认周一的 G2-D 从新位置跑通之后，再另行处理。

## 1 预检（只读；任何一项不满足就停下，报告给 John）

1. 没有 v6lite 进程，也没有旧 V6 进程（`trading_bot_v6_main`、`auto_trading_runner`、`discord_bot_runner`）在运行。检查结果只报告数量。
2. `C:\Dev\TradingV6\v6lite` 下 56 个文件和运行手册，与第十三批报告（`docs/V6_LITE_FIX_REPORT_ROUND13_2026-10-03.md`）的 57 项 SHA-256 清单逐项一致；`code_sha` 为 `d4b19f1d43b25448`。
3. 记录旧仓库的状态：当前分支、HEAD、`git status --porcelain` 的分类统计（修改了多少、删除了多少、未跟踪多少）。
4. 记录 `venv_v6` 的 Python 版本，以及 `pip freeze`。
5. 列出任务计划中与 TradingV6 有关的任务（任务名、动作、是否启用）。
6. 确认 `C:\Dev\V6Lite` 不存在；如果已经存在，就停下。

## 2 冻结旧 V6（在 `C:\Dev\TradingV6` 中操作）

| 步骤 | 内容 |
|---|---|
| A1 | `git switch -c legacy-v6-freeze-2026-10-03`。从当前 HEAD 新建分支，工作区不变，不影响原分支的历史。 |
| A2 | **暂存**：<br>• 已跟踪文件的修改和删除（`git add -u -- . ":!v6lite" ":!docs/V6_LITE_*"`）；<br>• 未跟踪的**源码和文档类**文件（`*.py *.md *.toml *.txt *.ps1 *.sh *.bat *.json` 模板）。<br>**排除**：`v6lite/`、`docs/V6_LITE_*`、`state/`、`logs/`、`data/`、`evidence/`、`exports/`、`reports/`、`_temp*/`、`tradingv6_round2_acceptance/`、`venv*`、`ibc_windows/*.ini`、图片、根目录的 `today_*.txt`，以及 `.gitignore` 已经忽略的一切。 |
| A3 | **提交前检查**，有异常就停下报告，不提交：<br>• 对暂存区做机密扫描：`.env` 内容、token、password、`discord.com/api/webhooks`、SMTP 凭据等模式；<br>• 列出所有超过 5 MB 的文件；<br>• 报告暂存了多少文件、按目录分组的统计。 |
| A4 | `git commit -m "Freeze legacy V6 working tree (2026-10-03)"`，再打附注 tag：`git tag -a v6-frozen-2026-10-03 -m "Legacy V6 frozen; V6-Lite moved to C:\Dev\V6Lite"`。**不 push。** |
| A5 | **停用启动入口**，只重命名，可以还原：<br>• 把 `start_v6.bat`、`start_trading.sh`、`watchdog.sh`、`setup_launchd.sh` 分别改名，加后缀 `.frozen`；<br>• 根目录新增 `FROZEN_README.md`：说明冻结日期和 tag，说明 V6-Lite 已迁到 `C:\Dev\V6Lite`，并写出还原方法；<br>• 提交：`Disable legacy launchers`。<br>`start_v6.bat` 已被 `.gitignore` 忽略，只改名，不纳入提交。 |
| A6 | **任务计划**：动作明确会启动旧 V6 的任务，执行 `Disable-ScheduledTask`（禁用，不删除），并报告任务名。拿不准的任务不要动，列出来交给 John。 |

## 3 建立 V6-Lite 独立仓库 `C:\Dev\V6Lite`

| 步骤 | 内容 |
|---|---|
| B1 | **复制**：<br>• 把 `C:\Dev\TradingV6\v6lite\` 复制到 `C:\Dev\V6Lite\v6lite\`，排除 `__pycache__`，**逐字节一致**；<br>• 把 `C:\Dev\TradingV6\docs\V6_LITE_*` 复制到 `C:\Dev\V6Lite\docs\`。<br>复制后：新位置的 `code_sha` 必须为 `d4b19f1d43b25448`，57 项清单全部一致。 |
| B2 | 新建 `.gitattributes`，内容为 `* -text`，禁止 git 转换换行，保证 checkout 出来的字节不变、`code_sha` 稳定。<br>新建 `.gitignore`：`__pycache__/`、`.pytest_cache/`、`.venv/`、`*.sqlite3`、`*.log`、`/v6lite.*.toml`（John 本地的运行配置不入库；样例在 `v6lite/examples/` 中，已经入库）。 |
| B3 | `git init -b main`。git 用户名和邮箱沿用 `C:\Dev\TradingV6` 仓库的设置。提交 `V6-Lite batch 13 (code_sha d4b19f1d43b25448)`，打 tag `v6lite-batch13`。在提交说明中写明：第 1–13 批的历史以各批报告中的 SHA-256 清单为准。<br>**验证**：把仓库 `git clone` 到 `%TEMP%` 下的一个新目录，在克隆里算 `code_sha`，必须仍是 `d4b19f1d43b25448`。 |
| B4 | **新 venv** `C:\Dev\V6Lite\.venv`：<br>• 用和 `venv_v6` 相同版本的 Python 创建；<br>• 依赖只装 V6-Lite 实际 import 到的包（先扫描 `v6lite` 的 import 来确认），版本固定为 `venv_v6` 中的版本。预期包括：<br>  – `ib-insync==0.9.86`<br>  – `eventkit==1.0.3`<br>  – `nest-asyncio==1.6.0`<br>  – `psutil==7.2.2`<br>  – `tzdata==2026.1`（Windows 上 `zoneinfo` 必须靠它）<br>  – `pytest==9.0.3` 及其依赖<br>• 写入 `requirements.txt`（`pip freeze` 的结果）；<br>• 如果 `g2_probe --compare-yfinance` 需要 yfinance，单独写一份 `requirements-probe.txt`，同样固定版本；<br>• **不改 `venv_v6`**。 |
| B5 | **验证新环境**：<br>• 用新 venv 跑全量测试 3 次，设置 `V6LITE_LEGACY_SCANNER=C:\Dev\TradingV6\market_scanner.py`，让策略对比测试也真正运行，预期 443 passed；<br>• `python -m v6lite --help` 正常；<br>• 打印 `ib_insync.__version__`。 |
| B6 | **运行配置**：<br>• 把 John 的 `C:\Dev\TradingV6\v6lite.paper.toml` 复制为 `C:\Dev\V6Lite\v6lite.paper.toml`；<br>• 由 `v6lite\examples\paper_delayed.toml` 生成 `C:\Dev\V6Lite\v6lite.paper.delayed.toml`；<br>• 两份配置的 `foreign_lock_paths` 都改成指向旧仓库的**绝对路径**：`C:/Dev/TradingV6/state/trading_bot_v6.lock` 和 `C:/Dev/TradingV6/trading_bot.lock`（第十二批 G8 起，相对路径按配置文件所在目录解析，搬家后不改就会失效）；<br>• `state_dir` 保持不变（仍在 `%USERPROFILE%\.v6lite\` 下），已有的演练状态继续可用；<br>• 在报告里给出两份配置与样例的 diff。 |
| B7 | **运行手册**（新仓库的 `docs\V6_LITE_G2_RUNBOOK.md`）：<br>• 所有路径改为 `C:\Dev\V6Lite`；<br>• 激活命令改为 `.\.venv\Scripts\Activate.ps1`；<br>• 写明旧位置已停用、仅作为回退；<br>• 增加“每批交付流程”一节（见第 4 节）。<br>提交 `Runbook for standalone repo and venv`。 |
| B8 | **旧位置的 `C:\Dev\TradingV6\v6lite\` 不动**：不删除、不改名、不加文件，作为周一的回退。之后如何处理，由 John 在周一 G2-D 成功后另行决定。 |

## 4 今后的流程（写进运行手册）

- **改代码不再覆盖正在使用的版本。** 第十三批是在原地覆盖第十二批写的，结果周一原定要用的第十二批代码已经不在磁盘上。今后每批都以 git tag 为准，John 可以 `git checkout v6lite-batchNN` 回到任意一个已复核的版本。
- Codex 只在 `C:\Dev\V6Lite` 中改代码。
- 每批完成后：Claude 复核通过，John 同意，由 Codex `git commit` 并打 tag `v6lite-batchNN`。报告里的 SHA-256 清单照常提供。
- 不 push 到远程；是否建私有远程仓库，由 John 另行决定。

## 5 约束

- 不连接 Gateway，不运行引擎和探针（`--help` 除外）。
- 不删除任何文件：只做复制、重命名，以及新建上面列出的文件。
- 不改 `.env`、`state/`、`%USERPROFILE%\.v6lite\`，不改旧 V6 的 python 代码，不改 `AGENTS.md`、`docs/codex_memory.md`。
- 不输出任何机密，包括 webhook URL、bot token、SMTP 密码和 `.env` 内容。
- **预检第 1、2、6 项不满足，或 A3 的检查发现异常，立即停下**，等 John 决定。
- **时限**：如果 OPS-1 没能在周一 06:00 PT 之前完成并经 Claude 复核，周一照旧从 `C:\Dev\TradingV6` 用 `venv_v6` 运行第十三批。代码完全相同，`code_sha` 也相同。

## 6 交付

报告 `C:\Dev\V6Lite\docs\V6_LITE_OPS1_REPORT_2026-10-0X.md`，同时在 `C:\Dev\TradingV6\docs\` 中放一份副本。内容包括：
- 预检结果；
- 冻结：分支名、两个 commit 的哈希、tag，暂存文件的分类统计，排除的类别，机密扫描结果，改名的启动脚本，被禁用的任务；
- 新仓库：commit 哈希和 tag，克隆验证的 `code_sha`，`git ls-files` 的数量；
- 新 venv：Python 版本，`requirements.txt`，3 次全量测试的结果；
- 两份配置的 diff，运行手册改动的摘要；
- **John 周一早上的完整命令**（从新位置启动 G2-D）。

另附一份给 John 转交 Claude 的摘要，不超过 20 行。
