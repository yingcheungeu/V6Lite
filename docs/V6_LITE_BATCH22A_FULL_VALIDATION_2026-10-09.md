# V6-Lite batch22a 全量验收补充报告（2026-10-09）

第9条补丁的默认全量连续3次已通过。本报告补充第9条冻结报告，不改写原报告或原200项manifest。

- code_sha：`f27e144de9eb406b`；父提交：`42c77da139ce308ca133eb7e2e40beaf33ecac9d`。
- 原第9条报告 SHA-256：`e0df7b68600349591b1a2c1fd3223ea413d7c3903e6b15b2625a9b1025a5eecf`。
- 原manifest SHA-256：`53c79f52d84f7c376cbc477fc91d85a4ddf5c4e532340db48fbdbaf90a3e0357`，200/200仍一致。
- 首次测试实际1344 passed+1 skipped，但冻结runner在计算目录哈希时失败，不计入gate；原始TXT/XML和initial_tooling_failure.json保留。新文档工具修目录摘要后另连续3次验收，交易源码未改。
- 默认全量：v6lite/tests + scripts/tests；使用dev .venv，-B、PYTHONUTF8=1、PYTHONDONTWRITEBYTECODE=1，禁用pytest cache和第三方插件自动加载。
- 每轮开始/结束均在交易时段限制之外；源码digest每轮前后不变；未连接券商、未读取运行仓库或User归档。
- 第9条新增42项及已有保护回归291项先前均已通过；两冻结基线红证据和全部中间失败保留。

Python：`3.11.9 (tags/v3.11.9:de54cf5, Apr  2 2024, 10:12:12) [MSC v.1938 64 bit (AMD64)]`；Windows：`Windows-10-10.0.26300-SP0`。

| 次数 | 结果 | 实际PT开始—结束 | stdout SHA-256 | JUnit SHA-256 |
|---|---|---|---|---|
| 1 | 1344 passed / 1 skipped / 0 failed / 0 errors | 2026-10-09T13:37:06.670792-07:00 — 2026-10-09T13:41:43.366625-07:00 | `c79a9b55fd5a204509c5efac763d45f128c54d446664b6d68e73691185879d00` | `00cda5792b78adc06b8d8d69574da355f722340bb67431ef21c4ef8456011d1d` |
| 2 | 1344 passed / 1 skipped / 0 failed / 0 errors | 2026-10-09T13:41:43.924499-07:00 — 2026-10-09T13:46:19.717515-07:00 | `bc05a38c1dd9d3e01d3d2e58a43c28fa7799f1ddb7ddbdafd56205f3d4bd8c62` | `16e67399338e2480ab1e84ff6c09af57b235c55b7fd1d7f5eb39cf2c3122d2de` |
| 3 | 1344 passed / 1 skipped / 0 failed / 0 errors | 2026-10-09T13:46:20.275003-07:00 — 2026-10-09T13:51:01.342929-07:00 | `3e467cf0772e1cf8952fa2807e169296fb21dc8b8908c02bcd07443a6c4ede7a` | `a2b9e32c865bd403e66ff41f403021783651853a6e7f6d0245d1588e009b9af2` |

## 提交范围

仅第9条6生产文件、5测试文件、冻结第9条报告和证据目录，以及本次补充报告/证据。不包含Claude操作手册、第二十三批指令、item8研究、运行配置、数据库或缓存。旧v6lite-batch22不移动；本次将新增附注tag v6lite-batch22a。

## SHA-256 清单

新清单覆盖完整v6lite、全部第9条证据（包含三次全量JSON/TXT/XML和独立审签收据）、本补充报告与证据工具。排除新manifest本身和最终独立审签收据以避免循环；原manifest与冻结报告独立保留。

