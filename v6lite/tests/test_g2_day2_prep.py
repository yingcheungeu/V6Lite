"""Findings while preparing G2 day 2 (2026-10-02, real Paper Gateway):

D2-1  the Discord webhook delivered from PowerShell but the engine got
      HTTP 403 for every alert: Discord's edge rejects Python's default
      User-Agent. The channel now identifies itself, and an HTTP error
      reports Discord's reason (never the secret URL).
D2-2  an unreachable Gateway printed a traceback: one clear line instead.
"""

from __future__ import annotations

import io
import urllib.error
import urllib.request

import pytest

from v6lite.alerts import DiscordWebhookChannel
from v6lite.tests.helpers import make_cfg

SECRET = "https://discord.com/api/webhooks/123/SECRET-TOKEN"


class _Resp:
    status = 204

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_D2_1_webhook_request_carries_an_explicit_user_agent(monkeypatch):
    seen = {}

    def urlopen(req, timeout):
        seen["ua"] = req.get_header("User-agent")
        seen["ct"] = req.get_header("Content-type")
        return _Resp()
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    DiscordWebhookChannel(SECRET).send("subject", "body")
    assert seen["ua"] and not seen["ua"].startswith("Python-urllib")
    assert seen["ct"] == "application/json"


def test_D2_1_http_error_reports_status_but_never_body_or_url(monkeypatch):
    def urlopen(req, timeout):
        raise urllib.error.HTTPError(SECRET, 403, "Forbidden", {}, io.BytesIO(b'{"message": "error code: 1010"}'))
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(RuntimeError) as err:
        DiscordWebhookChannel(SECRET).send("subject", "body")
    assert "403" in str(err.value) and "1010" not in str(err.value)
    assert "SECRET-TOKEN" not in str(err.value)


def test_D2_2_unreachable_gateway_is_one_clear_line(tmp_path, capsys, monkeypatch):
    from v6lite import __main__ as cli
    cfg_path = tmp_path / "c.toml"
    cfg_path.write_text(f"""
mode = "paper"
[ibkr]
host = "127.0.0.1"
port = 4002
client_id = 131
account = "DU0000000"
[universe]
tradable = ["AAPL"]
scan = ["AAPL"]
[sectors]
AAPL = "tech"
[paths]
state_dir = "{(tmp_path / 's').as_posix()}"
""", encoding="utf-8")

    def refuse(*a, **k):
        raise ConnectionRefusedError(1225, "refused")
    monkeypatch.setattr(cli, "cmd_run", refuse)
    rc = cli.main(["run", "--config", str(cfg_path), "--dry-run"])
    err = capsys.readouterr().err
    assert rc == 3 and "CANNOT CONNECT" in err and "127.0.0.1:4002" in err and "Traceback" not in err
