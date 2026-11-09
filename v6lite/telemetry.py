"""Presentation-only account masks and bounded normal query statistics."""
from __future__ import annotations
import copy
import json
import logging
import re

_ACCOUNT = re.compile(r"(?<![A-Za-z0-9])(?:DFA|DU|DF|U|F|I)\d{4,}(?![A-Za-z0-9])")


def mask_account(account):
    value = str(account or "")
    if len(value) <= 3:
        return "***"
    prefix = re.match(r"[A-Za-z]+", value)
    return (prefix.group(0) if prefix else "") + "******" + value[-3:]


def redact_accounts(text, account=None):
    if account:
        text = text.replace(str(account), mask_account(account))
    return _ACCOUNT.sub(lambda match: mask_account(match.group(0)), text)


class AccountFormatter(logging.Formatter):
    def __init__(self, fmt, account=None):
        super().__init__(fmt)
        self.account = account

    def format(self, record):
        # Formatting a copy avoids exposing or altering args/exc_text for a
        # later handler. Redact after traceback and exception interpolation.
        return redact_accounts(super().format(copy.copy(record)), self.account)


class QueryStatistics:
    def __init__(self, logger):
        self.logger = logger
        self.channels = {}
        self.started = None

    def completed(self, channel, now, seconds, drained, *, success=True):
        if self.started is None:
            self.started = now
        item = self.channels.setdefault(channel, {"count": 0, "max_seconds": 0.0, "drained": 0,
                                                  "success": 0, "failed": 0})
        item["count"] += 1
        item["success" if success else "failed"] += 1
        item["max_seconds"] = max(item["max_seconds"], seconds)
        item["drained"] += int(bool(drained))
        if now - self.started >= 1800:
            self.flush(now)

    def flush(self, now):
        if self.channels:
            self.logger.info("[QUERY_SUMMARY] %s", json.dumps({"channels": self.channels}, sort_keys=True))
            self.channels = {}
        self.started = now


class ExecutionStatistics:
    """Only normal repeated log output is compressed, never native evidence."""
    def __init__(self, logger):
        self.logger = logger
        self.signatures = {}
        self.pending = {}

    def record(self, *, role, key, signature, now, monotonic, counts, sample, abnormal=False):
        fmt = "[EXECUTION_TIME_SUMMARY] returned=%d zoned=%d naive=%d unparseable=%d sample=%s"
        args = (counts["returned"], counts["zoned"], counts["naive"], counts["unparseable"], sample)
        self.logger.debug(fmt, *args)
        if abnormal:
            self.logger.warning(fmt + " incomplete_or_invalid=True role=%s", *args, role)
            self.signatures.pop(key, None)
            return
        if self.signatures.get(key) != signature:
            self.logger.info(fmt, *args)
            self.signatures[key] = signature
            return
        item = self.pending.setdefault(key, {"role": role, "suppressed_queries": 0,
            "returned": 0, "zoned": 0, "naive": 0, "unparseable": 0,
            "first_at": now.isoformat(), "last_at": now.isoformat(), "_started": monotonic})
        item["suppressed_queries"] += 1
        item["last_at"] = now.isoformat()
        for field in ("returned", "zoned", "naive", "unparseable"):
            item[field] += counts[field]
        if monotonic - item["_started"] >= 1800:
            self.flush(key=key)

    def flush(self, *, key=None, reset=False):
        for group in list(self.pending):
            if key is not None and key != group:
                continue
            item = self.pending[group]
            self.logger.info("[EXECUTION_TIME_AGGREGATE] %s", json.dumps(
                {name: value for name,value in item.items() if not name.startswith("_")}, sort_keys=True))
            del self.pending[group]
        if reset:
            self.signatures.clear()
