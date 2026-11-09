"""Render the fact-only BT-1 report from deterministic, sanitized artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BT = Path(__file__).resolve().parent
OUT = BT / "out"
REPORT = ROOT / "docs" / "V6_LITE_BT1_REPORT_2026-10-03.md"
LEDGER = Path(r"C:\Dev\TradingV6\state\tpsl_exit_ledger.json")


def money(value):
    return "—" if value is None else f"{value:+,.2f}"


def number(value, digits=2):
    return "—" if value is None else f"{value:,.{digits}f}"


def table(headers, rows):
    return "\n".join(["| " + " | ".join(headers) + " |",
                      "| " + " | ".join("---" for _ in headers) + " |",
                      *("| " + " | ".join(str(v) for v in row) + " |" for row in rows)])


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full-tests", default="待最终串行验证")
    ap.add_argument("--reproducibility", default="待最终串行验证")
    args = ap.parse_args()
    audit = json.loads((OUT / "data_audit.json").read_text(encoding="utf-8"))
    summaries = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
    scenarios = summaries["scenarios"]
    sensitivity = summaries["sensitivity"]
    paper = json.loads((OUT / "paper_crosscheck.json").read_text(encoding="utf-8"))
    parts = ["# BT-1：V6-Lite 策略离线日线回测", "",
             "日期：2026-10-03（PT）。数据与交易逻辑只读；没有连接 Gateway、联网、改参数或提交。",
             "", "## 口径和可复现性", "",
             "- 运行：`C:\\Dev\\V6Lite\\.venv\\Scripts\\python.exe -B -m research.bt1.backtest`，"
             "随后运行 `-B -m research.bt1.paper_crosscheck`；只访问本机历史库、旧 Paper 账本。",
             "- 历史库以 SQLite URI `mode=ro&immutable=1` 打开；11 条目标日线取 `timeframe='1d' AND source='ibkr'`。"
             f"数据库 SHA-256：`{audit['db_sha256']}`；`PRAGMA integrity_check={audit['integrity_check']}`。",
             f"- 旧 Paper 账本 SHA-256：`{sha(LEDGER)}`。",
             "- `v6lite.version.code_sha()`：运行前 `d4b19f1d43b25448`；运行后 `d4b19f1d43b25448`。"
             "研究代码在 `research/bt1/`，未改 `v6lite/`。",
             f"- 输出 CSV 再生成逐字节校验：{args.reproducibility}。哈希清单见文末。",
             "", "## 数据体检", "",
             "缺失日按 `v6lite.market_calendar.is_trading_day`，统一覆盖 2021-06-14—2026-08-06；"
             "每行的缺日详列在表内。2025-01-09 被该日历标为交易日，但 11 条序列共同无数据；"
             "不自行填充，当日无行情，次日 D−1 缺失则不产生信号。", ""]
    audit_rows = []
    for symbol, item in audit["symbols"].items():
        audit_rows.append((symbol, item["rows"], item["start"], item["end"],
                           len(item["missing_days"]), "<br>".join(item["missing_days"]),
                           len(item["invalid_rows"]), len(item["overnight_jumps"])))
    parts += [table(("标的", "根数", "首日", "末日", "缺日数", "缺失交易日", "非法价/量行", "隔夜>25%"), audit_rows), "",
              "OHLC 无空值、非有限值或 ≤0；重复交易日均为 0；`adjusted_close` 在 11 条序列中非空根数均为 0。"
              "严格隔夜口径为 `open_D / close_(D−1) − 1`。10 个交易标的均无超 ±25% 的隔夜跳变；"
              "^VIX 的全部 3 条如下。额外的 close-to-close 跳变单列于 `data_audit.json`，不冒充隔夜变化。", "",
              table(("^VIX 日期", "前收", "当日开", "隔夜涨跌"),
                    [(j["day"], j["previous_close"], j["open"],
                      f"{j['change_pct']:+.4f}%") for j in audit["symbols"]["^VIX"]["overnight_jumps"]]), "",
              "AMZN 2022-06-03/06 前收与后开为 122.35/125.25，GOOGL 2022-07-15/18 为 "
              "111.78/112.61，TSLA 2022-08-24/25 为 297.10/302.38。"
              "这三个已知拆股节点附近没有明显未调整倍数跳变，但库中 `adjusted_close` 全空且没有调整来源证明，"
              "因此**不能确认**整段历史已按拆股调整；此项是结果可信度限制。",
              "^VIX 最后记录 2026-06-11，按日历到 08-06 缺 39 日；"
              "绝不沿用旧 VIX 值，D−1 无 VIX 时交给 `effective_min_score(None,...)`，门槛为 75。"
              "IWM/IBB 有个别标的内部缺口；持仓首次遇该缺口即截尾，余量按前次有效收盘**仅作账面结转**，"
              "不算可验证成交，也不计完整交易统计。缺失 D−1 的标的不产信号。"
              "GOOGL、META、TSLA、IWM、IBB 等序列末尾缺日同样不补。全部数据最多到 2026-08-06，"
              "08-07 至当前没有日线，本批不补数据。", "", "## 回测规则与简化", "",
              "- 直接调用 `v6lite.strategy.generate_signals/effective_min_score`、"
              "`v6lite.exits.compute_levels/decide_exit/breakeven_stop/sell_limit/buy_limit`、"
              "`v6lite.sizing.size_entry/estimate_fee` 以及 `v6lite.risk.daily_loss_limit`；"
              "配置读 `v6lite/examples/paper.toml`。一切为离线模拟，无券商委托。",
              "- D 日信号只使用 D 以前的数据；VIX 精确取日历定义的 D−1 收盘。"
              "A/C/D 用前 90 根，B 用 D 前 60 个日历日的有效根数。每天两次真实扫描共享同一批日线，"
              "因此这里只在 D 日开盘作一次决策；信号数计一次而非两次。",
              "- 先按前持仓的 D 日开盘处理跳空止损，再用 D 日开盘与 D−1 信号价做 3% 偏离门控；"
              "入场价为开盘×(1+滑点)，基准 5bp。定仓用生产 `buy_limit(开盘,0.3%)` 价格"
              "调用 `size_entry`，实际入场现金另核对含手续费。最多 2 持仓、板块最多 1、"
              "每日最多 4 入场；资金上限 $10,000，单笔风险/仓位/订单金额按原配置。",
              "- 入场当天也评估 D 日高低。止损/止盈判定全部调用 `decide_exit`；"
              "跳空越过止损按开盘，日内同时触原止损和 TP 按原止损先；"
              "TP 按生产 `sell_limit` 扣 1% 保护限价差。TP1 后移保本；"
              "同日 TP1 后若 low 触新保本位，保守地假定随后保本止损，优先于 TP2/TP3。"
              "日线无法识别真实盘中先后顺序，也无法证明保护限价真的成交。",
              "- 每次成交基准扣 `estimate_fee`；单笔从入场到全数出场才算完整交易，"
              "R 的分母为初始股数×配置 2.5 ATR。连续亏损冷却按当天余下时间禁入；"
              "日内亏损停止只可在开盘已知损益阻断此轮，盘中变动后没有第二次决策。"
              "无报价新鲜度、点差、订单队列、部分成交与真实资金可用性数据，因此这些门控无法逐笔复原。",
              "- 盈亏与胜率、平均 R、PF、分组均只统计**完整平仓**；最终权益曲线含未平仓按最后日线收盘估值及截尾账面结转。"
              "回撤是每日权益峰值回撤；持有天数为日历日；资金占用率为每日持仓市值除以固定 $10,000 后取均值。",
              "", "## 四个方案", "",
              "A=90 根/60（VIX 高时75）；B=60 个日历日/60（高时75）；"
              "C=90 根/50（高时75）；D=90 根/70（高时75）。"
              "`score_symbol` 的 BUY 判定本身硬编码 score≥60，因此 C 的 50 并不会放出 50–59 的信号；"
              "C 与 A 的信号、入场和权益轨迹相同，交易 CSV 只因方案前缀 ID 不同而哈希不同。", ""]
    overall = []
    for key, v in scenarios.items():
        overall.append((key, v["signals_total"], v["entries_total"], v["trades"],
                        f"{v['signals_per_month']:.2f}", f"{v['entries_per_month']:.2f}",
                        f"{v['win_rate_pct']:.2f}%", number(v["avg_r"], 3),
                        number(v["expectancy_r_per_trade"], 3), number(v["profit_factor"], 3),
                        money(v["net_pnl_usd"])))
    parts += [table(("方案", "信号", "入场", "完整交易", "信号/月", "入场/月", "胜率",
                    "平均 R", "期望 R/笔", "PF", "完整净盈亏 $"), overall), ""]
    risk_rows = []
    for key, v in scenarios.items():
        risk_rows.append((key, money(v["total_pnl_usd_including_open_mtm"]),
                          money(v["max_drawdown_usd"]),
                          f"{v['max_drawdown_pct_of_10000']:.2f}%",
                          number(v["avg_holding_calendar_days"], 1),
                          f"{v['capital_occupancy_pct']:.2f}%",
                          v["censored_data_gap"], v["open_at_end"],
                          money(v["closed_trade_fees_usd"]),
                          f"{v['fees_pct_of_gross_profit']:.2f}%"))
    parts += [table(("方案", "期末权益盈亏 $", "最大回撤 $", "占初始资金", "平均持有日",
                    "资金占用", "数据截尾", "尾端持仓", "完整交易佣金 $", "佣金/毛利"), risk_rows), "",
              "权益盈亏与完整交易盈亏分母不同，不能把含截尾估值的期末权益当作已实现策略利润。"
              "逐方案勾稽如下（前三个数值列相加等于权益盈亏）：", "",
              table(("方案", "完整平仓净 $", "缺口截尾前收账面结转 $", "尾端持仓已实现+估值 $", "合计 $"),
                    [(key, money(v["net_pnl_usd"]),
                      money(v["censored_last_mark_proxy_pnl_usd"]),
                      money(v["open_mtm_and_partial_realized_pnl_usd"]),
                      money(v["total_pnl_usd_including_open_mtm"]))
                     for key, v in scenarios.items()]), "",
              "A/C 的 1 笔截尾是 IWM：2026-04-09 入场、2026-06-12 遇首个个别缺日，"
              "残余 2 股以前收 290.41 记账；该笔已有 TP1/TP2，但截尾余量没有真实可验证的退出成交。"
              "A/C 两笔尾端持仓为 SPY、PANW，其纯未实现估值为 +$127.3020，"
              "再加先前部分卖出净额后为 +$142.3816。", "",
              "### 出场阶段、时间段和分组", "",
              "阶段比例以完整交易为分母；同一笔可先达到 TP1/TP2，再以保本止损结束，故各列不可相加。", "",
              table(("方案", "原 ATR 止损", "保本止损", "TP1", "TP2", "TP3", "同日止损/止盈双触"),
                    [(key, *(f"{v['exit_reach_pct'][stage]:.2f}%" for stage in
                             ("ATR_STOP", "BREAKEVEN", "TP1", "TP2", "TP3")),
                      v["ambiguous_trade_days"]) for key, v in scenarios.items()]), ""]
    period_rows = []
    for key, v in scenarios.items():
        for label, g in v["groups"]["period"].items():
            period_rows.append((key, label, g["trades"], money(g["net_pnl_usd"]),
                                number(g["avg_r"], 3), number(g["profit_factor"], 3),
                                g["censored_data_gap"], g["open_at_end"]))
    parts += [table(("方案", "按入场日分段", "完整交易", "完整净盈亏 $", "平均 R", "PF",
                    "截尾", "尾端持仓"), period_rows), "",
              "下表把每个方案的完整交易按标的、入场年份及入场时 VIX 分组；"
              "无 VIX 的交易仍按严格门槛产生，空组 PF 记为“—”。", ""]
    for group_name, label in (("symbol", "标的"), ("year", "年份"), ("vix", "VIX 区间")):
        group_rows = []
        for key, v in scenarios.items():
            for group, g in v["groups"][group_name].items():
                group_rows.append((key, group, g["trades"], money(g["net_pnl_usd"]),
                                   "—" if g["win_rate_pct"] is None else f"{g['win_rate_pct']:.2f}%",
                                   number(g["avg_r"], 3), number(g["profit_factor"], 3)))
        parts += [f"#### 按{label}", "", table(("方案", label, "完整交易", "净盈亏 $", "胜率", "平均 R", "PF"),
                                              group_rows), ""]
    parts += ["## 基准 A 的敏感性", "",
              "以下每组从同一 $10,000 初始资金**重新跑组合路径**；滑点改变可能连入场集合也改变，"
              "故不是单笔逐项平移。", "",
              table(("条件", "入场", "完整交易", "完整净盈亏 $", "期末权益盈亏 $", "全部佣金 $", "最大回撤 $"),
                    [(label, v["entries_total"], v["trades"], money(v["net_pnl_usd"]),
                      money(v["total_pnl_usd_including_open_mtm"]), money(v["fees_usd"]),
                      money(v["max_drawdown_usd"])) for label, v in
                     (("5bp + 保守费用 + 止损优先（A）", scenarios["A"]),
                      ("0bp 滑点", sensitivity["slip_0bps"]),
                      ("10bp 滑点", sensitivity["slip_10bps"]),
                      ("不计佣金", sensitivity["fee_zero"]),
                      ("旧 Paper 费率", sensitivity["fee_paper"]),
                      ("同日先止盈", sensitivity["tp_first"]))]), "",
              "旧 Paper 费率为每笔成交 `max($1.00,$0.005×股数)`、无安全系数；"
              "保守费用在 A 全部已发生交易上合计 $412，旧费率 $206，不计费 $0。"
              "A 的同日原止损与 TP 双触次数为 0，故先止盈对照的总盈亏差为 $0；"
              "同日 TP1 后触新保本的保守顺序仍照既定规则。", "",
              "## 与旧 V6 Paper 账本交叉核对", "",
              "从 `state/tpsl_exit_ledger.json` 只提取 2026-04-30—08-19 的七个指定标的、"
              "出场事件日期（转美东日历日）及可用的最新价位计划。"
              "旧账本的出场 `created_at` **不等于原入场日**；部分计划是之后创建/迁移的快照。"
              "因此 ±3 交易日信号仅表示时间邻近，不证明同一策略来源或本可成交。"
              "16 个事件锚点逐项如下；括号中为 `信号日(score,信号价,ATR)`；"
              "08-06 之前可覆盖的窗口中 A 组合实际入场均为 0。", ""]
    event_rows = []
    for i, event in enumerate(paper, 1):
        sigs = event["a_signals_within_3_sessions"]
        signal_text = "<br>".join(
            f"{s['day']} ({s['score']}, {s['signal_price']}, {s['atr']})" for s in sigs) or "无"
        out_of_range = event["event_day"] > "2026-08-06"
        if out_of_range and not sigs:
            signal_text = "数据范围外；窗口无法完整比对"
        event_rows.append((i, event["event_day"], event["symbol"], event["event_type"],
                           "—" if event["old_entry_price"] is None else
                           number(event["old_entry_price"], 4), signal_text,
                           "范围外" if out_of_range else
                           len(event["a_entries_within_3_sessions"])))
    parts += [table(("#", "旧账本 ET 日", "标的", "事件", "旧入场价", "±3 日 A 信号 (分数,价,ATR)",
                    "A 入场数"), event_rows), "",
              "08-06 之后 A 数据已截断，08-11/08-19 账本事件的 ±3 日对照不完整；"
              "不能把“无信号”解释成该时段策略拒绝。旧 V6 含 Discord 情报入场，来源不一致的记录不强行归因。", "",
              "旧账本中当前仍保留完整 ATR/止损/TP 计划的四个标的如下；"
              "其他三个（GOOGL、SPY、TSLA）仅保留了旧入场价/出场事件，缺 ATR 和完整计划，无法逐项比较价位。", ""]
    plan_rows = []
    for symbol in ("ABT", "IBB", "IWM", "PANW"):
        p = next(e["old_plan"] for e in paper if e["symbol"] == symbol and
                 e["event_type"] == "latest_plan_created_or_migrated")
        plan_rows.append((symbol, number(p["entry_price"], 4), number(p["atr"], 4),
                          number(p["stop"], 2), number(p["tp1"], 2),
                          number(p["tp2"], 2), number(p["tp3"], 2)))
    parts += [table(("标的", "旧入场", "旧 ATR", "旧止损", "旧 TP1", "旧 TP2", "旧 TP3"),
                    plan_rows), "",
              "例如 ABT 旧计划 104.61/ATR 3.02；直接用未改的 `compute_levels` 固定点复算为"
              "止损 97.06、TP1 113.67、TP2 119.71、TP3 128.77，逐项一致。"
              "A 在 08-05 有 ABT 信号 105.46/ATR 3.07，在该窗口实际无入场；"
              "它是前一日收盘信号价而非旧 Paper 入场填单价，二者不可当作同一成交比较。"
              "PANW 07-29 计划附近 A 有 07-24/07-27 信号价 325.63/323.79、ATR 16.37/15.74；"
              "IBB 与 IWM 07-29 附近无 A 信号。完整可机读对照见 `paper_crosscheck.json`。", "",
              "## 测试、复算和局限", "",
              f"- 独立测试 Agent 写的聚焦单测：11 passed；覆盖跳空、双触止损优先、TP1 后保本、"
              "分段数量余数、费用、D 日无前视、3% 偏离、当天连亏禁入与零盈亏不重置。"
              f"V6-Lite 全量测试：{args.full_tests}。",
              "- 独立 Agent 用固定随机种子 20261004，从方案 A 的 70 笔完整交易中抽 5 笔；"
              "原始 SQLite `mode=ro&immutable=1` 逐笔复算 D−1 ATR、入场、每条出场、手续费、"
              "gross/net/R，5/5 一致：AAPL 2023-05-22→07-19（+$176.6450）、"
              "AAPL 一笔 −$95.0280、SPY 一笔 −$90.2304、ABT 一笔 −$101.2500、"
              "META 一笔 −$176.0070。抽样方法和断言在 `tests/sample_recalc.py`。",
              "- A 在前段 38 笔完整交易净 +$579.81、后段 32 笔仅 +$24.66；"
              "B 两段分别 −$464.05/−$323.29；C 与 A 重合；D 为 +$232.22/−$404.70。"
              "这说明观察对窗口与时期敏感；每年、标的、VIX 分组的样本量小，"
              "不得从四方案里挑最高收益当稳健结论。", "- 数据未证实拆股调整、"
              "VIX 右端缺失、个别标的缺口、尾端持仓截尾、日线不能恢复盘中先后、"
              "实际订单撮合/保护限价/报价新鲜度无法在这里证明。回测收益不是 Paper 或 Live 可实现收益。",
              "", "## SHA-256 清单", "",
              "以下为 `research/bt1/` 下全部源文件、独立测试与输出（跳过编译缓存）；"
              "报告本身不列入自身哈希清单，以免自引用。", "",
              "| 文件 | SHA-256 |", "| --- | --- |"]
    paths = sorted(p for p in BT.rglob("*") if p.is_file() and "__pycache__" not in p.parts
                   and p.suffix != ".pyc")
    for path in paths:
        parts.append(f"| `{path.relative_to(ROOT).as_posix()}` | `{sha(path)}` |")
    parts.append("")
    REPORT.write_text("\n".join(parts), encoding="utf-8", newline="\n")
    print(f"report={REPORT} bytes={REPORT.stat().st_size} sha256={sha(REPORT)}")


if __name__ == "__main__":
    main()
