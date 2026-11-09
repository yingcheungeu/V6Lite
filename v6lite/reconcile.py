"""Broker-truth reconciliation. Broker positions win; differences are never
auto-corrected. Any unexplained difference blocks NEW entries (exits keep
running for positions that do match)."""

from __future__ import annotations

from dataclasses import dataclass, field

from v6lite.models import BrokerPosition


@dataclass
class ReconcileResult:
    ok: bool
    diffs: list[str] = field(default_factory=list)
    unmanaged: list[str] = field(default_factory=list)   # broker holdings we do not manage
    mismatched_symbols: set[str] = field(default_factory=set)


def reconcile(*, managed: dict[str, int], broker: list[BrokerPosition],
              protected: set[str], ignored: set[str]) -> ReconcileResult:
    """managed: symbol -> quantity V6-Lite believes it holds (open positions).

    Rules
    * protected/ignored symbols: never managed. If V6-Lite ALSO thinks it manages
      one, that is a diff (it must not happen).
    * broker holding with no V6-Lite position and not protected/ignored ->
      'unmanaged' (operator must `ignore` it; v1 has no adopt).
    * quantity differences -> diff.
    * a short on an ignored symbol is still a diff (long-only engine).
    """
    result = ReconcileResult(ok=True)
    broker_qty: dict[str, float] = {}
    for pos in broker:
        broker_qty[pos.symbol.upper()] = broker_qty.get(pos.symbol.upper(), 0.0) + float(pos.quantity)

    for symbol in sorted(set(managed) | set(broker_qty)):
        mine = managed.get(symbol, 0)
        theirs = broker_qty.get(symbol, 0.0)
        excluded = symbol in protected or symbol in ignored
        if excluded:
            if mine:
                result.diffs.append(f"{symbol}: managed position on excluded symbol")
                result.mismatched_symbols.add(symbol)
            elif theirs < 0 and symbol not in protected:
                # a short is never "someone else's holding" to ignore: V6-Lite is
                # long-only, so a short on a tradable symbol can be its oversell
                result.diffs.append(f"{symbol}: short broker position {theirs:g} (ignore does not apply)")
                result.mismatched_symbols.add(symbol)
            continue
        if mine == 0 and theirs != 0:
            result.unmanaged.append(symbol)
            result.diffs.append(f"{symbol}: unmanaged broker holding {theirs:g}")
            result.mismatched_symbols.add(symbol)
        elif abs(theirs - mine) > 1e-9:
            result.diffs.append(f"{symbol}: expected {mine} broker {theirs:g}")
            result.mismatched_symbols.add(symbol)
    result.ok = not result.diffs
    return result
