# V6-Lite 第十八批整改报告（运维启动器）

日期：2026-10-05（PT）。初版交付时间：2026-10-05T22:03:12.512670-07:00；提交前补充时间：2026-10-05T22:35:17.058697-07:00。

## 0. 结论与范围

第十七批按 John 最新授权完成运行 main 的 no-ff 合并及附注运行 tag，code_sha=9725a81b8e5e3e11，运行仓库默认全量 740 passed。第十八批只在 C:\Dev\V6Lite-dev 的 dev 分支新增运维脚本、独立运维测试和更新手册；初版最终运维测试 106 passed；按 Claude 通过后 John 的提交前补充，新最终运维测试 112 passed、0 failed / 0 errors / 0 skipped（见第8节）。交易包 v6lite/ 与第十七批完全相同，前后 code_sha 都为 9725a81b8e5e3e11。

Claude 已复核第十八批初版，通过后 John 又授权本轮两项补充及 dev 提交。报告生成时尚未 stage/commit；补充通过独立复核后只提交五项任务文件并打附注 v6lite-batch18。本轮不提前合并或部署运行 main，迁移计划严格在10/6 13:30 PT之后、10/7 05:30 PT之前执行。它不阻塞 10/7 的既有原生命令 dry-run。没有连接 TWS/Gateway、启动真实 run/g2_probe/test-entry、push 或修改 John 的运行配置；未读取、写入、枚举或归档整个 %USERPROFILE%\.v6lite。未读取 .env、bot token 或实际 User webhook 值，测试使用合成值且只输出存在性和长度。运行配置只按授权加载并核对字节哈希，不输出内容或账户明文。

多 Agent 分工：根 Agent 是唯一生产脚本/文档实施者；测试 Agent 独立拥有 scripts/tests/test_v6_launchers.py；调查 Agent 只读核对 CLI/状态接口；独立复核 Agent 静态复核脚本和最终证据。最终验证由根 Agent 串行执行，冻结测试后没有其他 Agent 同时修改或运行测试。

## 1. 第十七批运行 main 合并记录

预检：Windows 进程证据未发现正在运行的 v6lite；运行 main 工作区干净。迁移前从 v6lite-batch17 的 Git 对象复算已批准报告 SHA-256 与 79 项清单：79/79 一致。批准报告 SHA-256：af2d5118bfd64e08c78c9ba315944f804ce3ea0ecbd7c6a0fabeec8bbf79d56c。

执行 John 授权的命令：

```powershell
git merge --no-ff v6lite-batch17 -m "Merge V6-Lite batch 17 (code_sha 9725a81b8e5e3e11) into runtime main"
```

无冲突，无需 abort；未 rebase 或改写已有历史。结果：

| 项目 | 结果 |
|---|---|
| 运行仓库 | C:\Dev\V6Lite，main |
| 新 HEAD | a5d9ff01cc176eba20590d49728007d2cb7338e4 |
| 父提交 1 | 768af43c2ce31320ee50a91d04734bbb93a10185 |
| 父提交 2 | 07661e8c1e4ec0a13f2059c6a47ae48d8c9396df（v6lite-batch17） |
| git diff --quiet v6lite-batch17 HEAD -- v6lite | PASS，退出码 0 |
| 运行 code_sha | 9725a81b8e5e3e11 |
| 附注 tag | v6lite-run-b17 |
| tag 对象 | 7054a83e2638fcd5492e98eb085b555ad3454909，类型 tag |
| tag 目标 | 新 HEAD a5d9ff01cc176eba20590d49728007d2cb7338e4 |
| 完成时间 | 2026-10-05 21:31:58 PT；在 13:30 之后、10/6 05:30 之前 |
| 最终工作区 | main 干净；第十八批仍仅在 dev |

使用运行仓库独立 .venv，默认保护设置全量一次：

```powershell
python -B -m pytest v6lite/tests -q -p no:cacheprovider --tb=short --junitxml=<TEMP证据路径>
```

开始 2026-10-05T21:29:10.372663-07:00，结束 2026-10-05T21:30:54.066410-07:00。实际结果：**740 passed in 102.12s**，退出码 0；JUnit 740 tests、0 failures、0 errors、0 skipped。V6LITE_LEGACY_SCANNER 指向冻结旧 V6 的 market_scanner.py 以运行原策略 parity 测试；Claude 沙盒的 739 passed + 1 skipped 与本机 740 passed 是旧扫描器可用性的环境差异（见 v6lite/tests/test_strategy_parity.py 的 skipif），不是放宽或删除测试。默认保护测试覆盖没有切换为诊断全集。

| 全量证据 | SHA-256 |
|---|---|
| TEMP/v6lite-run-b17-default-full.txt | 37c3dcdafdfe4a996f915cdc502e762a41eb57ad1a8c89bc3b375f8d045cb561 |
| TEMP/v6lite-run-b17-default-full.xml | e4bff2b78ddb55c874b559ac8aa6a89d19f7fba50df1a822aab972cc70d5eb52 |

两份现有根目录配置的授权加载 / 受保护 status 检查：

| 文件 | 加载 | port / client / data type | 指纹 | 字节是否变化 |
|---|---|---|---|---|
| v6lite.paper.toml | PASS | 4002 / 131 / 1 | e9128f2e9aff1ddf | 否 |
| v6lite.paper.delayed.toml | PASS | 4002 / 131 / 3 | a196a246bd79f7ce | 否 |

新增 protection 默认值可加载：enabled=true、trigger_unfilled_p0_seconds=60、steady_check_seconds=15、evidence_fail_p0_seconds=60；schema=4；formal/dry-run CLI 预检通过。没有把现有 4002 配置改成 7497，也没有创建 G3 配置。

**status 的实际验证边界：NOT_INSPECTED（真实状态库）。** 本轮同时禁止触碰整个用户状态目录，而原生 cmd_status 会走 Store 的可写/迁移路径。因此将 CLI state_path 替换为 TEMP 中明确不存在的 sentinel，给 open_store/Store/sqlite3.connect 设置拒绝守卫，只验证配置加载、预检及 cmd_status 无库控制流。守卫未触发、未创建目录。这不证明真实状态库是 NO_STATE，也不证明真实状态身份/数据已通过；不能把受保护控制流结果称为实际状态检查。上述禁触规则优先，未为完成 status 访问真实目录。

现有配置字节 SHA-256（加载前后相同）：

- v6lite.paper.toml：955a69dd14488142a4cb93c4675cc5f14e3924c7819ae1f24c72f857bc01ce90
- v6lite.paper.delayed.toml：1a63083301d95b3e477446326680615b31587771db2ef45098fcbf783b9532a4

## 2. 第十八批命令映射与限制

脚本固定使用自身所在仓库根及该仓库的 .venv/Scripts/python.exe；不下载依赖、不改配置或 User 环境变量、不设置永久 PATH。v6.cmd 使用 Windows PowerShell 5.1 并原样传递参数，返回子 PowerShell 退出码。手册给出绝对路径和当前窗口临时 PATH 用法。

| 命令 | 根目录配置 | 调度 |
|---|---|---|
| v6 dry | v6lite.paper.toml | run --config <绝对路径> --dry-run |
| v6 g3t-dry | v6lite.g3t.toml | run --config <绝对路径> --dry-run |
| v6 g3t | v6lite.g3t.toml | run --config <绝对路径> |
| v6 g3n-dry | v6lite.g3n.toml | run --config <绝对路径> --dry-run |
| v6 g3n | v6lite.g3n.toml | run --config <绝对路径> |
| v6 entry SYMBOL | 只能 v6lite.g3t.toml | test-entry SYMBOL --config <绝对路径> --operator John；标的转大写 |
| v6 status [mode] | 省略 mode 为 dry；显式选五种模式之一 | 独立只读摘要，精确选择 formal / test / dryrun |

每次真实执行前检查 main、HEAD 上的附注 v6lite-run-* tag（仅轻量 tag 或祖先 tag 不通过）、v6lite/ 的 staged/unstaged/untracked 修改；再用 tag/HEAD 对应 Git blob 复算完整 package 指纹，与磁盘 code_sha 比较，包内被忽略但参与指纹的额外文件也会被拦截。打印 HEAD、tag、code_sha。交互暂停后再次核对 HEAD/tag/package/config 字节，变化即拒绝。

所有模式只允许 Paper DU 账户与本机端点。G3-T 含 dry 要求 client132、test.enabled=true、qty3；G3-N 与普通 dry 为 client131、test关闭。全部 G3 模式强制 port7497、protection开启、实时行情；普通 dry 允许原 Paper 4002 或7497。缺配置、缺 venv、错误参数、任意参数透传、Live 配置或保护/行情不符合要求均 fail closed，不自动创建或修正。

非 dry 的 run（g3t/g3n）与 entry 必须在实际可交互 ConsoleHost、stdin 未重定向时，精确输入完整大写 YES；空输入、小写、空格和无交互均拒绝。status 不启动引擎/排队，属于只读查询，无需订单确认。-WhatIf 仅验证元数据、打印计划，不调度 CLI、不排队、不查询状态库、不要求 YES；预览不是启动批准。没有生产 --bypass、替代仓库/任意配置选项或非交互确认开关。

Webhook 只从 User 的 V6LITE_DISCORD_WEBHOOK 获取，输出是否存在与长度；真实 run/entry 缺值拒绝，status 或 WhatIf 可在缺值时提供只读结果/计划。实际执行仅暂时传入子进程 Process 环境，finally 恢复父环境，旧 Process 值不能代替 User 值；不输出 URL。只有测试函数替换为合成值，本轮未读取真实 User 值。

## 3. 独立只读 status 的接口依据

现有原生 CLI 不支持 status --dry-run；原生 status --config 固定正式/测试状态并构造 Store，可能 WAL/建表/迁移。为满足精确角色与物理只读，运维脚本自带独立 Python helper；没有修改 v6lite/ 或增加交易路径。

- SQLite URI 必须 mode=ro，PRAGMA query_only=ON，仅 SELECT；不使用 Store，不迁移、建库或重建。不存在目标库只报 NO_STATE，不回退到 formal 或其他角色。
- dry 目标 base/dryrun/v6lite.sqlite3、role=dryrun；G3-T 目标 base/v6lite.sqlite3、role=test；G3-N 目标 base/v6lite.sqlite3、role=formal。
- account/mode/role/schema/client 必须严格匹配；dry 数据库 client 是配置原值131/132，只有 API client 加1000。旧 schema / 身份错误安全拒绝，不修复数据。
- 输出持仓、active意图数、kill布尔值、连亏/冷却/异常数，以及真实 Engine health 的 broker_connected/reconcile_ok/entries_allowed、时间、age_seconds、180秒freshness及阻断数量。未上报 health 记 unknown；不输出账户明文、kill原因或任意备注。摘要不等同于原生 status 的全部输出。
- status 的 PowerShell 配置与查询代码均经 stdin 传给 python -，不以多行 -c 穿过 PowerShell 5.1 原生命令引号处理。

所有 status 测试均在 TEMP 的合成数据库上，检查查询前后全文件哈希未变；没有对 John 的实际状态调用该 helper。

## 4. 发现 → 改动 → 测试（自然红绿）

| 发现 | 改动 | 红测与绿测 |
|---|---|---|
| PowerShell 函数同时向 pipeline 返回子 Python stdout 与退出码，导致 exit 收到数组 | 非 Capture 输出转 Out-Host，只 return 整数退出码；cmd 原样传播退出码 | 独立真实 print/exit 0 和7子进程两例自然红（count=2），最终两例绿；cmd 原生参数/退出0/7绿 |
| status 使用虚构 connected/ready 键，遗漏 Engine 的实际连接/入场字段 | 使用 broker_connected/reconcile_ok/entries_allowed，未上报记 unknown | 实际 Engine health 形状自然红 KeyError broker_connected，最终绿且freshness断言保留 |
| Windows PowerShell 5.1 原生 -c 多行源码引号丢失，probe及status报 SyntaxError | 私有 InputCode 参数经 stdin 输送辅助程序，固定 argv 为 -B - probe/status <cfg> <dry>；真实 run/entry 参数未变 | 真 PS5.1 probe + dry/test status 三例自然红，最终三例绿；CLI和Broker未被创建 |

独立 Agent 起草测试，先在修复前启动器上执行：首轮 105 用例 95 passed / 10 failed，142.60秒。6项为上述产品红；另4项是夹具问题（Python转义 cmd /c 启动命令、G3测试状态末目录 test 命名、Git忽略规则误忽略 package 内 example TOML），仅修正夹具后转绿，未削弱安全断言。

修正夹具后的最终红基线：12 targeted = 6 failed / 6 passed / 93 deselected，17.37秒。6个产品红仍自然失败。根 Agent 此后唯一修改生产脚本。测试 Agent 只适配 -c→stdin 的私有传输 oracle，保留真实 PS5.1 helper / health / stdout / 安全断言；再按独立复核新增1条真实 stdin=PIPE、未mock Assert-V6Interactive 的拒绝测试，最终106用例。没有为交易包新建回归或放宽旧强断言。

| 红测证据（TEMP，保留原始记录） | SHA-256 |
|---|---|
| v6lite_batch18_launcher_red_20261005.log（首轮） | 3975ec1f1022015774c53327543c8c63814226d53da7abc9c52faf8464a8a01d |
| v6lite_batch18_launcher_final_red_20261005.log | 6620f58f55be221e5d3350d52eb88276c892346f738f5bfe9b8a1d076a8fe653 |
| 最终红基线测试源码（历史哈希，后仅适配传输和新增非交互测试） | 6b8af38cca1d0b23cc7fa3736dc556f2cfafa6c1fb7ca4832f02bb18e4ff35ab |

## 5. Windows 初版最终串行验证（Claude 已通过，保留原记录）

环境：Windows PowerShell 5.1.26100.9444（v6.cmd 使用 powershell.exe）；根控制 shell 为 PowerShell7.6.6。使用 dev 独立 venv，PYTHONDONTWRITEBYTECODE=1，禁用 pytest cache。全部 106 用例实际运行，未被 Linux skipif 跳过。

```powershell
python -B -m pytest scripts/tests/test_v6_launchers.py -q -p no:cacheprovider --tb=short --junitxml=<TEMP证据路径>
```

开始 2026-10-05T21:57:35.217567-07:00，结束 2026-10-05T22:00:07.519933-07:00。JUnit：106 tests、0 failures、0 errors、0 skipped，退出码0，耗时 150.310秒。PowerShell ParseFile、内嵌 Python AST、测试 AST 均 PASS，cmd 保留CRLF。

覆盖范围：五模式精确参数、WhatIf不调度、不提示确认、entry大小写/白名单/John、status五模式与默认dry、完整YES和非交互拒绝、G3全部错误端口拒绝、账户/client/test/protection/实时行情拒绝、真实Git脏包/错误分支/轻量或非HEAD tag拒绝、确认后配置/包漂移、User缺值/旧Process值不回退/环境还原、额外参数和安全错误、真实cmd参数/0与7退出码、原生Python stdout/int、状态角色/schema/client错配、不跨角色回退、不建缺库、查询前后字节不变、真实健康字段、忽略包文件指纹漂移、真实PS5.1配置/只读status传输、真实stdin重定向门槛。

| 最终证据（TEMP） | SHA-256 |
|---|---|
| v6lite-b18-final.log | 277aec9656cf3e3f3ba60e343653c928227d369d15ba34225e4baec1b18ffdf0 |
| v6lite-b18-final.xml | 2d5881065bf1032b763a54b6ed8c427c09ee7e8445074396aab9cbca1c72c361 |

最终重新核对：运行 main HEAD/tag/干净状态正确，v6lite-batch17与运行包 diff为空；dev包没有 staged/unstaged差异；运行和dev的 code_sha仍9725a81b8e5e3e11，两份运行配置哈希与加载前一致。本批没有修改交易代码，按运维范围跑该独立106项测试；未重复运行交易全量。没有 timing flake 或重试掩盖失败。

## 6. 手册与交付状态

更新 docs/V6_LITE_G3_RUNBOOK_DRAFT.md 至第4版：当前main第十七批部署证据、短命令映射、部署前提、临时PATH、WhatIf、精确YES、User环境来源、端口/client身份、status角色和只读边界。第十七批28项离线端口证据明确标为上一批证据，未声称本轮真实TWS验证。第十八批经 Claude 复核及获准合并并打运行tag后才使用；保留10/7的原生命令dry-run，Codex未执行。

第十八批开发分支为dev，提交前基线HEAD=07661e8c1e4ec0a13f2059c6a47ae48d8c9396df。任务修改只包含本报告、两启动脚本、一个独立测试文件、一本手册。John本轮已授权第十八批提交与附注tag、指定时间窗中的运行main合并；实际新commit/tag对象在提交后回报，不将本文件自身commit哈希写回以避免循环修改。无数据库、配置或机密作为新交付。

## 7. 第十八批文件 SHA-256 清单（提交前补充后的最终字节）

清单包含运维源码、测试及手册，共4项；v6lite/没有新增或修改，其已批准第十七批指纹不变。初版106项测试证据保留在第5节；本清单对应第8节补充后的112项测试。报告自身哈希单独交付，避免文件自引用。

| 文件 | 字节 | SHA-256 |
|---|---:|---|
| scripts/v6.ps1 | 15298 | 9818d068769592206e8b62bc65d4f52bc8bb98b2476c754fb04e2fba985e3d88 |
| scripts/v6.cmd | 161 | e6303971e31af8b57e050506ddae1c71516efc3f7c523850438fba3028fcc6a8 |
| scripts/tests/test_v6_launchers.py | 35142 | fff766c38064b7ad5421eadd9639f363e06cfa5a6e6ecb1448a4c22c002b270c |
| docs/V6_LITE_G3_RUNBOOK_DRAFT.md | 35684 | 55638f02ee03df929e0c82d2ac4e7b48c9207c926b238db748dd1a9f2594291f |

机器可复核清单：

```text
9818d068769592206e8b62bc65d4f52bc8bb98b2476c754fb04e2fba985e3d88  scripts/v6.ps1
e6303971e31af8b57e050506ddae1c71516efc3f7c523850438fba3028fcc6a8  scripts/v6.cmd
fff766c38064b7ad5421eadd9639f363e06cfa5a6e6ecb1448a4c22c002b270c  scripts/tests/test_v6_launchers.py
55638f02ee03df929e0c82d2ac4e7b48c9207c926b238db748dd1a9f2594291f  docs/V6_LITE_G3_RUNBOOK_DRAFT.md
```

## 8. Claude通过后的提交前补充、授权提交和定时迁移

Claude已通过初版：报告SHA-256=f40d3eeec4e151670aafe4c0c9d5fe9aa640060905e034cf1d6400927f19aa3a，v6.ps1=c18c1357088c9a4d9fd393f6638c18911f361d03233351d764274107332d9317，v6.cmd=e6303971e31af8b57e050506ddae1c71516efc3f7c523850438fba3028fcc6a8。本节为John最新授权的两个补充，不改v6lite/；v6.cmd保持原字节。

### 8.1 输出无缓冲：发现 → 改动 → 测试

发现：经Out-Host管道运行Python可能缓冲标准输出；原非Capture分支没有强制PYTHONUNBUFFERED。

改动：只在Invoke-V6Python非Capture分支，保存父Process PYTHONUNBUFFERED，设置1后启动子Python，在finally恢复原值/缺值。Capture probe/status不改环境；非Capture run/entry覆盖；返回仍仅整数，不混入stdout。没有持久化User环境变更。

测试：独立Agent新增6个真实TEMP子进程用例，先在c18c批准脚本上自然执行 **4 failed / 2 passed / 106 deselected，3.24秒**。4红为父值缺失/既有synthetic值×exit0/7，子进程读环境没有1；2个启动异常后环境恢复用例原本绿，是安全回归，不冒称产品红。测试随后冻结，根Agent唯一修脚本并串行最终验证。红测日志SHA-256：a64f0daa7a6ed9461a8766e2c86be9788fac6ba149b31a50cc28d06e254261be（TEMP/v6lite_batch18_launcher_unbuffered_red_20261005.log）。

新最终：**112 passed，0 failed / 0 errors / 0 skipped**；开始2026-10-05T22:31:37.654268-07:00，结束2026-10-05T22:33:58.414108-07:00；JUnit耗时138.143秒，退出码0。原106测试与新增6测试全部执行。ParseFile/AST与两仓库交易包指纹核对PASS；code_sha前后都为9725a81b8e5e3e11。

| 补充后最终证据（TEMP） | SHA-256 |
|---|---|
| v6lite-b18-supplement-final.log | 5fc53a5695ff7db0ab2e1c5c30fa936d0dce8d3b0efe16d619d03a0edbb3ecf4 |
| v6lite-b18-supplement-final.xml | c2b2005897055171b8fc4964274632744babd092216fce9532c6666f118fd76d |

### 8.2 Ctrl+C停机：文档化人工验收，未冒称实测

按John允许的“只写成文档化的人工检查步骤”交付运行手册第11.1节。112测试之后仅补充TEMP加载器、合成User变量和锁路径隔离的文档说明，生产脚本/测试未再改；第7节列最终手册字节，测试证据JSON保留测试时手册哈希，不覆盖历史记录。**本轮没有真实控制台Ctrl+C实测**，cmd的Y/N提示、PowerShell是否等待Python完整清理均为未实测，文档逐项规定记录其时间顺序；不会用普通子进程exit测试宣称Ctrl+C链路通过。

静态依据：__main__.py:222–228注册SIGINT/SIGBREAK只置停止flag；run_engine:284的finally调用shutdown，外层仍关闭告警/Store及释放锁。engine.py:2602–2603为dry-run SHUTDOWN审计后disconnect；:2663–2664为非dry审计unresolved/broker_working后disconnect。store.py:327–329仅写SQLite审计。**没有文本日志的shutdown complete或STOPPED行**，不能虚构。

真实可核对的“日志判定行”为当次SQLite audit中的SHUTDOWN：dry={"dry_run":true}；非dry={"broker_working":[],"unresolved":[]}。审计发生在disconnect前，单行不能证明完成：必须结合Python正常退出、PowerShell/cmd随后才返回、无断开/关闭异常；正式订单还须DAY撤单确认、GTC身份/股数与持仓正确、无停机P0/P1。dry-run不撤DAY、place/modify/cancel必须为0；另用离线非dry FakeBroker验证DAY取消/GTC保留，不能把dry只读结果当成真实撤单证据。15秒仅是等待段grace，不是全程限时。

手册唯一正常顺序：一次Ctrl+C，保持窗口，清理未证实时不选Y、不再中断、不强制杀进程，等待上述完整证据。出现Y/N时选N并记录，不以N为保证。人工验收通过前v6.cmd不作为G3真单入口，继续既有原生Python直接启动/停机流程；若验收发现包装器打断清理，停用并交复核，不自动写交易代码。此文档边界不改引擎、订单触发或门控。

### 8.3 授权范围、提交及后续运行迁移

成功的只读Windows进程枚举确认0个v6lite；权限受限的枚举报错不被视为0，重新以允许权限取证。只提交第7节4项文件加本报告，共5项；stage前后核对文件范围、哈希、机密URL/token/privatekey模式和大文件，禁止数据库、缓存、运行配置、.env或额外文件。完成后dev应干净，附注v6lite-batch18只指向本次提交；报告自身SHA-256、commit及tag提交后回报。

目前时间仍为10/5晚间，不能提前合并。计划安排 **10/6 13:35 PT** 在本线程执行一次获准迁移：先确认当前时间严格处于10/6 13:30 PT之后、10/7 05:30 PT之前，无v6lite进程，runtime main干净，dev提交/tag及清单正确；`git merge --no-ff v6lite-batch18`保留两边历史，冲突立即abort并报告。核对runtime v6lite相对v6lite-batch17不变、code_sha=9725a81b8e5e3e11、运维交付字节等于批次tag，打附注v6lite-run-b18。实际未执行本项时不声称main已更新；窗口过期或预检不满足就不迁移并报告。

迁移任务不读取/修改John根配置，不接触整个%USERPROFILE%\.v6lite，不连接TWS/Gateway，不push、不输出任何机密。13:35之后需要的运维验证只使用TEMP合成环境；不在交易日06:00–13:30 PT跑全量。
