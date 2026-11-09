"""Immutable, validated, fingerprinted V6-Lite configuration.

One TOML file is the only source of trading parameters. Environment variables
are used ONLY for secrets (SMTP password, webhook URL) and never change risk or
routing behaviour. The config fingerprint binds the live approval file to the
exact parameters that were approved.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

PAPER_PORTS = {4002, 7497}
LIVE_PORTS = {4001, 7496}
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ConfigError(ValueError):
    pass


SECTION_KEYS = {
    "strategy": {"scan_times_et", "min_score", "high_vix_threshold", "high_vix_min_score",
                 "bars_lookback_days", "atr_stop_mult", "tp1_mult", "tp2_mult", "tp3_mult",
                 "breakeven_offset_pct"},
    "risk": {"max_capital_usd", "risk_per_trade_pct", "max_position_pct", "max_order_notional_usd",
             "max_positions", "max_per_sector", "max_daily_entries", "daily_loss_limit_usd",
             "daily_loss_limit_pct", "max_consecutive_losses", "loss_cooldown_minutes",
             "entry_window_start_et", "entry_cutoff_minutes_before_close", "commission_estimate_min_usd"},
    "execution": {"entry_limit_band_pct", "exit_limit_band_pct", "max_spread_pct", "max_quote_age_seconds", "tp_max_quote_age_seconds",
                  "entry_fill_timeout_seconds", "exit_fill_timeout_seconds", "exit_max_retries",
                  "cancel_grace_seconds", "reconcile_interval_seconds", "exit_check_interval_seconds",
                  "tp_start_after_open_seconds"},
}
TOP_LEVEL_KEYS = {"mode", "ibkr", "universe", "sectors", "strategy", "risk", "execution", "paths", "alerts", "test", "protection"}
QUERY_INTERVAL_FIELDS = {"open_orders_min_interval_seconds", "positions_min_interval_seconds",
                         "executions_min_interval_seconds"}
IBKR_KEYS = {"host", "port", "client_id", "account", "market_data_type"} | QUERY_INTERVAL_FIELDS
UNIVERSE_KEYS = {"tradable", "scan", "etf", "protected"}
PATH_KEYS = {"state_dir", "foreign_lock_paths"}
# integer fields and their minimum value (bool and float are rejected)
INT_MIN = {
    "port": 1, "client_id": 1, "market_data_type": 1, "min_score": 0, "high_vix_min_score": 0,
    "bars_lookback_days": 30, "max_positions": 1, "max_per_sector": 1, "max_daily_entries": 1,
    "max_consecutive_losses": 1, "loss_cooldown_minutes": 1, "entry_cutoff_minutes_before_close": 0,
    "tp_max_quote_age_seconds": 1, "max_quote_age_seconds": 1, "entry_fill_timeout_seconds": 1, "exit_fill_timeout_seconds": 1,
    "exit_max_retries": 1, "cancel_grace_seconds": 1, "reconcile_interval_seconds": 10,
    "exit_check_interval_seconds": 1, "tp_start_after_open_seconds": 0,
}
FLOAT_FIELDS = {
    "high_vix_threshold", "atr_stop_mult", "tp1_mult", "tp2_mult", "tp3_mult", "breakeven_offset_pct",
    "max_capital_usd", "risk_per_trade_pct", "max_position_pct", "max_order_notional_usd",
    "daily_loss_limit_usd", "daily_loss_limit_pct", "entry_limit_band_pct", "exit_limit_band_pct",
    "max_spread_pct", "commission_estimate_min_usd",
}


@dataclass(frozen=True)
class AlertConfig:
    email_to: str = ""
    email_from: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password_env: str = "V6LITE_SMTP_PASSWORD"
    discord_webhook_env: str = "V6LITE_DISCORD_WEBHOOK"
    require_remote_in_live: bool = True


@dataclass(frozen=True)
class TestConfig:
    enabled: bool = False
    qty: int = 3
    stop_mult: float = 0.3
    tp1_mult: float = 0.3
    tp2_mult: float = 0.5
    tp3_mult: float = 0.8


@dataclass(frozen=True)
class ProtectionConfig:
    enabled: bool = True
    order_type: str = "STP"
    gap_a_p1_seconds: int = 5
    gap_a_p0_seconds: int = 15
    gap_b_cancel_seconds: int = 10
    gap_b_p0_seconds: int = 30
    trigger_unfilled_p0_seconds: int = 60
    steady_check_seconds: int = 15
    evidence_fail_p0_seconds: int = 60
    overnight_tp: str = "freeze"


@dataclass(frozen=True)
class LiteConfig:
    # --- mode / broker ---
    mode: str                      # "paper" | "live"
    account: str
    host: str = "127.0.0.1"
    port: int = 4002
    client_id: int = 131           # distinct from legacy V6 (120)
    market_data_type: int = 1      # 1=live, 3=delayed (paper rehearsal only)
    open_orders_min_interval_seconds: float = 1.0
    positions_min_interval_seconds: float = 1.0
    executions_min_interval_seconds: float = 1.0
    # --- universe ---
    scan_symbols: tuple[str, ...] = ()
    tradable_symbols: tuple[str, ...] = ()
    etf_symbols: tuple[str, ...] = ()
    protected_symbols: tuple[str, ...] = ()
    sectors: tuple[tuple[str, str], ...] = ()
    # --- strategy (ported from market_scanner.py) ---
    scan_times_et: tuple[str, ...] = ("10:00",)
    min_score: int = 60
    high_vix_threshold: float = 30.0
    high_vix_min_score: int = 75
    bars_lookback_days: int = 90
    atr_stop_mult: float = 2.5
    tp1_mult: float = 3.0
    tp2_mult: float = 5.0
    tp3_mult: float = 8.0
    breakeven_offset_pct: float = 0.0005
    # --- risk ---
    max_capital_usd: float = 10_000.0
    risk_per_trade_pct: float = 0.01
    max_position_pct: float = 0.50
    max_order_notional_usd: float = 5_000.0
    max_positions: int = 2
    max_per_sector: int = 1
    max_daily_entries: int = 4
    daily_loss_limit_usd: float = 150.0
    daily_loss_limit_pct: float = 0.015
    max_consecutive_losses: int = 2
    loss_cooldown_minutes: int = 60
    commission_estimate_min_usd: float = 1.0  # accounting only; sizing reserve is unchanged
    entry_window_start_et: str = "10:00"
    entry_cutoff_minutes_before_close: int = 30
    # --- execution ---
    entry_limit_band_pct: float = 0.003
    exit_limit_band_pct: float = 0.002
    max_spread_pct: float = 0.005
    max_quote_age_seconds: int = 15
    tp_start_after_open_seconds: int = 30
    tp_max_quote_age_seconds: int = 3
    entry_fill_timeout_seconds: int = 60
    exit_fill_timeout_seconds: int = 45
    exit_max_retries: int = 3
    cancel_grace_seconds: int = 5
    reconcile_interval_seconds: int = 300
    exit_check_interval_seconds: int = 30
    # --- infra ---
    state_dir: str = ""
    foreign_lock_paths: tuple[str, ...] = ()
    alerts: AlertConfig = field(default_factory=AlertConfig)
    test: TestConfig = field(default_factory=TestConfig)
    protection: ProtectionConfig = field(default_factory=ProtectionConfig)

    # ------------------------------------------------------------------
    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    @property
    def is_test(self) -> bool:
        return self.test.enabled

    def sector_of(self, symbol: str) -> str:
        return dict(self.sectors).get(symbol.upper(), symbol.upper())

    def resolved_state_dir(self) -> Path:
        if self.state_dir:
            return Path(self.state_dir).expanduser()
        return Path.home() / ".v6lite" / self.mode

    def fingerprint(self) -> str:
        """SHA-256 over every behaviour-relevant field (state_dir excluded)."""
        payload = asdict(self)
        payload.pop("state_dir", None)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------
def _symbols(values, name: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if not isinstance(values, (list, tuple)):
        raise ConfigError(f"{name} must be a list")
    out: list[str] = []
    for raw in values:
        sym = str(raw).strip().upper()
        if not _SYMBOL_RE.match(sym):
            raise ConfigError(f"{name}: invalid symbol {raw!r}")
        if sym not in out:
            out.append(sym)
    return tuple(out)


def _finite_positive(value, name: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"{name} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be a number") from exc
    if not math.isfinite(number) or number < 0 or (number == 0 and not allow_zero):
        raise ConfigError(f"{name} must be finite and > 0")
    return number


def _check_types(cfg: LiteConfig) -> None:
    for name in QUERY_INTERVAL_FIELDS:
        value = getattr(cfg, name)
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                not math.isfinite(value) or value < 1):
            raise ConfigError(f"ibkr.{name} must be finite and >= 1 second")
    for name, minimum in INT_MIN.items():
        value = getattr(cfg, name)
        if type(value) is not int:
            raise ConfigError(f"{name} must be an integer (got {value!r})")
        if value < minimum:
            raise ConfigError(f"{name} must be >= {minimum}")
    for name in FLOAT_FIELDS:
        value = getattr(cfg, name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ConfigError(f"{name} must be a finite number (got {value!r})")
        if value < 0:
            raise ConfigError(f"{name} must not be negative")
    if cfg.tp_max_quote_age_seconds > 10:
        raise ConfigError("tp_max_quote_age_seconds must be 1..10")
    if cfg.market_data_type not in {1, 3}:
        raise ConfigError("market_data_type must be 1 (live) or 3 (delayed)")
    if cfg.min_score > 100 or cfg.high_vix_min_score > 100:
        raise ConfigError("scores are 0..100")
    if not 0 <= cfg.breakeven_offset_pct < 0.05:
        raise ConfigError("breakeven_offset_pct must be in [0, 0.05)")
    if cfg.high_vix_threshold <= 0:
        raise ConfigError("high_vix_threshold must be > 0")


def _check_alerts(alerts: "AlertConfig") -> None:
    port = alerts.smtp_port
    if type(port) is not int or not 1 <= port <= 65535:
        raise ConfigError(f"alerts.smtp_port must be an integer 1..65535 (got {port!r})")
    for name in ("email_to", "email_from", "smtp_host", "smtp_user", "smtp_password_env", "discord_webhook_env"):
        if not isinstance(getattr(alerts, name), str):
            raise ConfigError(f"alerts.{name} must be a string")
    if type(alerts.require_remote_in_live) is not bool:
        raise ConfigError("alerts.require_remote_in_live must be true or false")


def validate(cfg: LiteConfig) -> LiteConfig:
    _check_types(cfg)
    protection = cfg.protection
    if type(protection.enabled) is not bool:
        raise ConfigError("protection.enabled must be true or false")
    if type(protection.overnight_tp) is not str or protection.overnight_tp not in {"freeze", "constraint"}:
        raise ConfigError('protection.overnight_tp must be "freeze" or "constraint"')
    if protection.order_type != "STP":
        raise ConfigError('protection.order_type must be "STP"')
    for name in ("gap_a_p1_seconds", "gap_a_p0_seconds", "gap_b_cancel_seconds", "gap_b_p0_seconds",
                 "trigger_unfilled_p0_seconds", "steady_check_seconds", "evidence_fail_p0_seconds"):
        value = getattr(protection, name)
        if type(value) is not int or value < 1:
            raise ConfigError(f"protection.{name} must be a positive integer")
    if protection.gap_a_p1_seconds >= protection.gap_a_p0_seconds or protection.gap_b_cancel_seconds >= protection.gap_b_p0_seconds:
        raise ConfigError("protection gap warning/cancel time must precede P0")
    if cfg.mode not in {"paper", "live"}:
        raise ConfigError("mode must be 'paper' or 'live'")
    if type(cfg.test.enabled) is not bool:
        raise ConfigError("test.enabled must be true or false")
    if type(cfg.test.qty) is not int or cfg.test.qty != 3:
        raise ConfigError("test.qty must be 3 shares for this G3 test mode")
    multipliers = (cfg.test.stop_mult, cfg.test.tp1_mult, cfg.test.tp2_mult, cfg.test.tp3_mult)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
           for value in multipliers) or not (0 < multipliers[0] and 0 < multipliers[1] < multipliers[2] < multipliers[3]):
        raise ConfigError("test ATR multipliers must be finite; stop > 0 and 0 < tp1 < tp2 < tp3")
    if cfg.test.enabled:
        if cfg.mode != "paper":
            raise ConfigError("test mode is only allowed in paper mode")
        if not cfg.account.startswith("DU"):
            raise ConfigError("test mode requires a DU paper account")
        if not cfg.state_dir.strip():
            raise ConfigError("test mode requires an explicit isolated state_dir")
        parts = [part.lower() for part in Path(cfg.state_dir).expanduser().resolve().parts]
        if any(part in {"paper", "paper-delayed", "dryrun"} for part in parts):
            raise ConfigError("test mode state_dir must be isolated from paper and rehearsal state")
        if "test" not in parts[-1]:
            raise ConfigError("test mode state_dir name must contain 'test' to identify its isolated purpose")
    account = cfg.account.strip()
    if not account:
        raise ConfigError("ibkr.account is required")
    if cfg.mode == "paper":
        if not account.startswith("DU"):
            raise ConfigError("paper mode requires a DU paper account")
        if cfg.port not in PAPER_PORTS:
            raise ConfigError(f"paper mode requires a paper port {sorted(PAPER_PORTS)}")
    else:
        if account.startswith("DU"):
            raise ConfigError("live mode cannot use a DU paper account")
        if cfg.port not in LIVE_PORTS:
            raise ConfigError(f"live mode requires a live port {sorted(LIVE_PORTS)}")
        if cfg.market_data_type != 1:
            raise ConfigError("live mode requires real-time market data (market_data_type=1)")
        if not protection.enabled:
            raise ConfigError("live mode requires broker-side protection")
    if not 1 <= cfg.client_id <= 32767 or cfg.client_id == 120:
        raise ConfigError("client_id must be 1..32767 and must not reuse legacy V6 client 120")
    if protection.enabled and cfg.client_id != (132 if cfg.test.enabled else 131):
        raise ConfigError("protected formal runs require client_id=131; protected test runs require 132")
    if not cfg.tradable_symbols:
        raise ConfigError("universe.tradable must not be empty")
    missing = set(cfg.tradable_symbols) - set(cfg.scan_symbols)
    if missing:
        raise ConfigError(f"tradable symbols missing from scan list: {sorted(missing)}")
    overlap = set(cfg.tradable_symbols) & set(cfg.protected_symbols)
    if overlap:
        raise ConfigError(f"protected symbols cannot be tradable: {sorted(overlap)}")
    for hhmm in (*cfg.scan_times_et, cfg.entry_window_start_et):
        if not _HHMM_RE.match(hhmm):
            raise ConfigError(f"invalid HH:MM time {hhmm!r}")
    _finite_positive(cfg.max_capital_usd, "risk.max_capital_usd")
    _finite_positive(cfg.max_order_notional_usd, "risk.max_order_notional_usd")
    _finite_positive(cfg.daily_loss_limit_usd, "risk.daily_loss_limit_usd")
    _finite_positive(cfg.commission_estimate_min_usd, "risk.commission_estimate_min_usd")
    for name in ("risk_per_trade_pct", "max_position_pct", "daily_loss_limit_pct",
                 "entry_limit_band_pct", "exit_limit_band_pct", "max_spread_pct"):
        value = _finite_positive(getattr(cfg, name), name)
        if value >= 1:
            raise ConfigError(f"{name} must be a fraction < 1")
    if cfg.risk_per_trade_pct > 0.02:
        raise ConfigError("risk_per_trade_pct above 2% is not allowed")
    if not (0 < cfg.atr_stop_mult and 0 < cfg.tp1_mult < cfg.tp2_mult < cfg.tp3_mult):
        raise ConfigError("ATR multipliers must satisfy 0<stop and tp1<tp2<tp3")
    _check_alerts(cfg.alerts)
    if cfg.is_live and cfg.alerts.require_remote_in_live is not True:
        raise ConfigError("live mode cannot disable required remote alerts")
    if cfg.is_live:
        email_ok = bool(cfg.alerts.email_to and cfg.alerts.smtp_host)
        webhook_ok = bool(cfg.alerts.discord_webhook_env and os.environ.get(cfg.alerts.discord_webhook_env))
        if not (email_ok or webhook_ok):
            raise ConfigError("live mode requires a configured remote alert channel "
                              "(email smtp settings or a Discord webhook env var)")
    return cfg


def load_config(path: str | Path) -> LiteConfig:
    config_path = Path(path).expanduser().resolve()
    raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    unknown = set(raw) - TOP_LEVEL_KEYS
    if unknown:
        raise ConfigError(f"unknown top-level config keys: {sorted(unknown)}")
    if str(raw.get("mode", "paper")).strip().lower() == "live" and "test" in raw:
        raise ConfigError("live config must not contain a [test] section")
    for section, allowed in (("ibkr", IBKR_KEYS), ("universe", UNIVERSE_KEYS), ("paths", PATH_KEYS),
                             ("alerts", set(AlertConfig.__dataclass_fields__)),
                             ("test", set(TestConfig.__dataclass_fields__)), *SECTION_KEYS.items()):
        extra = set(raw.get(section, {})) - allowed
        if extra:
            raise ConfigError(f"[{section}] has keys that do not belong there: {sorted(extra)}")
    ib = raw.get("ibkr", {})
    uni = raw.get("universe", {})
    strat = raw.get("strategy", {})
    risk = raw.get("risk", {})
    exe = raw.get("execution", {})
    paths = raw.get("paths", {})
    alerts = raw.get("alerts", {})
    test = raw.get("test", {})
    protection = raw.get("protection", {})
    if "limit_offset_pct" in protection:
        raise ConfigError("保护单已改为 STP，不再使用 limit_offset_pct，请从配置中删除")
    extra = set(protection) - set(ProtectionConfig.__dataclass_fields__)
    if extra:
        raise ConfigError(f"[protection] has keys that do not belong there: {sorted(extra)}")

    scan = _symbols(uni.get("scan"), "universe.scan")
    tradable = _symbols(uni.get("tradable"), "universe.tradable")
    sectors_raw = raw.get("sectors", {})
    sectors = tuple(sorted((str(k).upper(), str(v)) for k, v in sectors_raw.items()))

    kwargs: dict = {
        "mode": str(raw.get("mode", "paper")).strip().lower(),
        "account": str(ib.get("account", "")).strip(),
        "host": str(ib.get("host", "127.0.0.1")),
        "port": ib.get("port", 4002),
        "client_id": ib.get("client_id", 132 if test.get("enabled", False) is True else 131),
        "market_data_type": ib.get("market_data_type", 1),
        **{name: ib.get(name, 1.0) for name in QUERY_INTERVAL_FIELDS},
        "scan_symbols": scan or tradable,
        "tradable_symbols": tradable,
        "etf_symbols": _symbols(uni.get("etf"), "universe.etf"),
        "protected_symbols": _symbols(uni.get("protected"), "universe.protected"),
        "sectors": sectors,
        "state_dir": str(paths.get("state_dir", "")),
        "foreign_lock_paths": tuple(
            str((config_path.parent / Path(p).expanduser()).resolve())
            for p in paths.get("foreign_lock_paths", [])),
        "alerts": AlertConfig(**alerts),
        "test": TestConfig(**test),
        "protection": ProtectionConfig(**protection),
    }
    for section in (strat, risk, exe):
        for key, value in section.items():
            if key == "scan_times_et":
                value = tuple(str(v) for v in value)
            kwargs[key] = value
    return validate(LiteConfig(**kwargs))
