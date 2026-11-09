# Batch31 收盘后续办

当前候选冻结，未提交/tag。最终聚焦focused_final_2：170通过，126.703秒，source_unchanged=True；旧候选更广413项聚焦通过后又修复了佣金交错与诊断问题，不能把413项称为最终同一源码全量。

- dev HEAD / b30基线：86cbb435db0ab501d5bcb5cc242e2e1fac881555。
- 指令SHA-256：dffca92d2370d9d608bd555fca3de77ad03fa553945cbfc2a38949c0237a56cc。
- code_sha：f65631caea374a49。
- package SHA-256：f65631caea374a498ca5fdacb9805bd2f074e3de0a985fa183380f314e111699。
- inventory SHA-256：45a38d8409bbcd65ca652650b04005775fbb3cbb1220fc1709ed50f729f85289。
- 202文件；193个Python源码compile/UTF8 AST/无BOM；预期全量2247例。

先核对freeze.json的全部源码、验证工具和SDK哈希、当前HEAD/dev、原指令hash。不得编辑冻结源码；变更必须保留旧freeze及回执、重新聚焦验证/编译/复核/冻结，并重启三次连续全量。原始红测与失败回执不覆盖。

## 尚缺的验收裁定

原指令§3.4要求真实b30冻结升级后自动解除。旧generic marker同时涵盖真实身份/数量错误与待确认，原审计无完整tuple，不能安全证明唯一原因。新明确cause的解除契约通过，但没有生产产生该精确cause的现存路径，不能算真实旧记录迁移集成通过。原始13:33–13:35现场的全部6轮拒绝，也不能仅凭价格摘要唯一复原；已证明正常查询清空proof时过夜ACK丢失的机制，保持此证据限度。

人类用户已收到异步选择题：接受保守保留来源不明旧冻结，或保持原验收等待完整旧证据。没有回复不算批准。若用户明确接受前者，才可在scope_resolution.json记录：authorized_by="user"、decision="accept_conservative_legacy_freeze"、quote=用户原话、source=直接用户消息。不得伪造、代批或由其他Agent消息推出授权。如果用户提供新旧证据，先调查；严格原要求未满足时不得提交/tag。

裁定尚无也继续执行独立的收盘后测试与报告收口。

## 仅在安全时间串行全量

用真实时钟确认PT >= 2026-10-27 13:30，且不在任何交易日06:00–13:30。激活 C:\Dev\V6Lite-dev\.venv，设置PYTHONUTF8=1/PYTHONIOENCODING=utf-8。仅串行：

```powershell
python -B docs/batch31_evidence/v6lite_b31_validate.py final_full_1 full
python -B docs/batch31_evidence/v6lite_b31_validate.py final_full_2 full
python -B docs/batch31_evidence/v6lite_b31_validate.py final_full_3 full
```

每次等结束并检查rc=0、完整XML、2247例、同一inventory/code_sha、source_unchanged=True、日志/XML哈希，再下一次。已有标签的任何产物都不覆盖。保留失败并调查；第三次同一测试flake必须修复或人类明确重新定义门槛，不能重试掩盖。三次记录必须没有夹入全量失败且完整case身份相同。全量前脚本会强制核对冻结工具/源码；锁如残留，先核对PID和实际进程，不盲删锁。

## 收口与条件交付

三次通过后运行：

```powershell
python -B docs/batch31_evidence/v6lite_b31_closeout.py final
```

它生成实际全量报告、卡片样例、SHA256SUMS、closeout和C:\tmp\v6lite_b31_stage_paths.json。没有scope_resolution时，报告仍写验收待裁定，保持未提交/tag；不要绕开门禁。

只有直接用户明确裁定且报告引用其原话、三次全量都通过后，才运行：

```powershell
python -B docs/batch31_evidence/v6lite_b31_git_deliver.py commit
```

精确allowlist暂存、本地dev提交、annotated v6lite-batch31；核验tag archive全部inventory、package/code_sha、report和manifest blob哈希，并读取C:\tmp\v6lite_b31_delivery.json。tag存在或部分提交先核对真实状态，不覆盖tag、不重复提交、不unstage外来文件。

不push、不merge、不部署；不连接TWS/Gateway、不发真实订单、不碰用户.v6lite、运行仓库、John配置或操作手册。保留其他未跟踪历史证据和现有操作手册改动。交付后冻结等待Claude复核；不发邮件/Discord/其他聊天。此次续办只在完成、失败、重要变化或需要用户裁定时通知，完成后停止。
