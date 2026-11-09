# Batch31 收盘后续办（R2，真实证据顺序校准）

当前候选冻结，尚未提交/tag。最终聚焦 focused_final_3：187 passed，133.031秒，source_unchanged=True。R1候选及报告/工具保存在 *_r1 文件，原始失败回执全部保留。不得用旧候选回执代替当前三次全量。

- dev HEAD / b30基线：86cbb435db0ab501d5bcb5cc242e2e1fac881555。
- 指令SHA-256：dffca92d2370d9d608bd555fca3de77ad03fa553945cbfc2a38949c0237a56cc。
- code_sha：13c2c871fb98222c。
- package SHA-256：13c2c871fb98222c0a16db3d858e710a3f6f881a9620413965d193898e5ff526。
- inventory SHA-256：495cf1cf32ea4773b1202b0016561009987b0b4bc9bb2a121fe98b007b546f7e。
- 204个源码文件；195个Python源码compile/UTF8 AST/无BOM；预期完整2264例（b30 2222 + 新42）。

先核对 freeze.json 的全部源码/工具/SDK哈希、当前HEAD/dev、原指令hash，以及 read_only_inputs。用户提供的 docs/batch31_claude_evidence/ 只读；manifest SHA-256为80c4d9646e2708b11b3f1c135bfbdad3a5f5beaeda22b62e02c95861d5633e00，6文件逐项匹配。不得修改该目录，不得加入allowlist。v6lite_b31_inputs.py只读复核；引用哈希不代表纳入暂存。

## 用户裁定已经明确

scope_resolution.json 保存直接人类原话：接受保守规则；新冻结写明原因；仅“修改待确认”在BE确认且新完整a–d通过后自动解除/审计/INFO；来源不明的旧b30冻结保持不变。无需再次索要该授权。用户也要求三次全量通过后本地提交/tag，再冻结等复核。

真实审计已校准回放：10/27原生openOrder在自己的openOrders请求中，107.325ms到达，2ms后End；没有BE orderStatus；TP1在BE之前已由带时区reqExecutions验证。最终spec在原b30先失败（red_b30_evidence_query_End_final），候选通过。10/26是95.086ms查询外live ACK。具体来源门为b30 protection_confirmations.py:193–196；13:33:27首次P1对应overnight_evidence.py:512–513复合门，P0对应protection_engine.py:1173–1182。原生状态/数量/重复行数未记录，不能编造复合门内部哪个子项失败；报告保留这一限度。此前receipt-proof空窗为独立缺陷，不是本次现场根因。

当前完整自己openOrders ACK要求同socket、未poisoned、sent==ended==event request，改价后发起、完整End、唯一精确工作单。重启仍完整三项；ACK不授予TP。新增快照负向16项及旧冻结后补原因已覆盖。187项最终聚焦全部通过。

## 13:30 PT 后串行全量

用真实时钟确认 PT >= 2026-10-27 13:30，且不在任何交易日06:00–13:30。激活 C:\Dev\V6Lite-dev\.venv，仅串行执行：

```powershell
python -B docs/batch31_evidence/v6lite_b31_validate.py final_full_1 full
python -B docs/batch31_evidence/v6lite_b31_validate.py final_full_2 full
python -B docs/batch31_evidence/v6lite_b31_validate.py final_full_3 full
```

每次等结束并检查rc/failure/error=0、完整XML2264例、同一inventory/code_sha、source_unchanged及read_only_inputs_unchanged、日志/XML哈希，再下一次。三次case身份/skip集合一致，没有夹入全量失败。不得并行或覆盖已有/部分产物；新尝试用新label并在closeout显式传三label。第三次同一测试flake必须修复或人类明确重新定义门槛，不得重试掩盖。锁如残留先核对PID/真实进程，不盲删。

失败保留并调查；需要修改源码时先保留当前freeze/报告/工具，再修复、聚焦验证、重新冻结，随后重启三次连续全量。工具也已冻结，不能偷偷改门禁。不要删除任何历史失败证据。

## 完成后交付

三次通过后：

```powershell
python -B docs/batch31_evidence/v6lite_b31_closeout.py final
python -B docs/batch31_evidence/v6lite_b31_git_deliver.py commit
```

closeout生成实际报告、卡片样例、SHA256SUMS、closeout和 C:\tmp\v6lite_b31_stage_paths.json。git_deliver精确allowlist暂存、本地dev提交和annotated v6lite-batch31，核验tag归档inventory/package/code_sha、report/manifest blob、读取 C:\tmp\v6lite_b31_delivery.json。只读Claude证据目录和操作手册均显式排除。tag已存在或部分提交先核对，不覆盖tag、不重复提交、不unstage外来文件。完整报告是提交前生成，最终提交hash在delivery回执和用户最终回复中给出，避免改已核验tag。

不push、merge、部署，不连接TWS/Gateway，不发真实订单，不碰用户.v6lite/运行仓库/John配置/操作手册。保留其他历史未跟踪文件和操作手册现有改动。交付后冻结等待Claude复核并停止；不发其他聊天/邮件/Discord。仅完成、失败、重要变化或需要用户输入时通知，状态不变保持安静。
