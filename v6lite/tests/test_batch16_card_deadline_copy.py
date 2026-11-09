"""Phone cards state the configured escalation deadline, not the P1 warning time."""

from __future__ import annotations

import re

import pytest

from v6lite.config import ProtectionConfig
from v6lite.tests.helpers import Rig


@pytest.mark.parametrize("p0_seconds", [15, 21])
def test_A_gap_warning_says_P0_is_measured_from_gap_start_at_configured_deadline(
        tmp_path, p0_seconds):
    rig = Rig(tmp_path, protection=ProtectionConfig(gap_a_p0_seconds=p0_seconds))
    try:
        rig.open_aapl()
        row = rig.store.protections()[0]
        rig.engine._protection_page(rig.clock.now(), "P1", "gap A warning", row,
                                    "P1 at five seconds after the BUY fill")
        subject, body = rig.channel.messages[-1]
        action = body.split("\n你要做什么：", 1)[1]
        impact = body.split("\n影响：", 1)[1].split("\n你要做什么：", 1)[0]
        assert "P1" in subject and "P0" in action
        assert re.search(rf"(?<!\d){p0_seconds}\s*秒", action + impact)
        assert any(word in action + impact for word in ("空窗起点", "空窗开始", "成交回报", "累计"))
        assert "若五秒仍无券商确认，立即按 P0" not in action
    finally:
        rig.store.close()


def test_trigger_crossing_card_uses_configured_unfilled_P0_deadline(tmp_path):
    rig = Rig(tmp_path, protection=ProtectionConfig(trigger_unfilled_p0_seconds=47))
    try:
        rig.open_aapl()
        row = rig.store.protections()[0]
        rig.engine._protection_page(rig.clock.now(), "P1", "trigger crossing", row,
                                    "mark crossed the stop trigger")
        subject, body = rig.channel.messages[-1]
        action = body.split("\n你要做什么：", 1)[1]
        assert "P1" in subject and "P0" in action
        assert re.search(r"(?<!\d)47\s*秒", action)
        assert "六十秒" not in action
    finally:
        rig.store.close()
