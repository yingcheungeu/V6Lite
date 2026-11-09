"""V6-Lite: the slim live-trading core for Trading V6.

Design: docs/V6_LITE_REDESIGN.md

V6-Lite is a separate entry point. It does not import `trading_bot_v6` and does
not read or write the legacy V6 runtime state. Its state lives outside the
repository (default: ~/.v6lite/<mode>/).
"""

__version__ = "0.1.0"
