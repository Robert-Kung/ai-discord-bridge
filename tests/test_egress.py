"""Egress canary classification (spec: egress-containment).

The three-probe classifier must fail closed on BOTH a live direct route and an
allow-all proxy — the latter is the misconfig a connectivity-only canary misses.
"""
import json
from pathlib import Path

from bridge import config, egress

REPO = Path(config.__file__).resolve().parent.parent
DENY = json.loads((REPO / "settings.json").read_text())["permissions"]["deny"]


def test_ok_only_when_contained_and_anthropic_up():
    # direct fails, proxy denies control, proxy reaches anthropic → contained
    assert egress.classify_egress(False, False, True) == egress.EGRESS_OK


def test_direct_route_present_refuses():
    # a live direct route means no isolation at all — most severe, checked first
    assert egress.classify_egress(True, False, True) == egress.EGRESS_OPEN
    assert egress.classify_egress(True, True, True) == egress.EGRESS_OPEN


def test_allow_all_proxy_refuses():
    # THE key case: direct route gone, but the proxy tunnels the control host
    # (empty/broken ACL). A connectivity-only canary would pass this; we must not.
    assert egress.classify_egress(False, True, True) == egress.EGRESS_OPEN_PROXY
    # even if anthropic is also reachable, an allow-all proxy is still a refusal
    assert egress.classify_egress(False, True, False) == egress.EGRESS_OPEN_PROXY


def test_anthropic_down_is_transient():
    # contained, but the proxy can't reach anthropic → retryable, not a refusal
    assert egress.classify_egress(False, False, False) == egress.EGRESS_ANTHROPIC_DOWN


def test_status_constants_distinct():
    vals = {egress.EGRESS_OK, egress.EGRESS_OPEN,
            egress.EGRESS_OPEN_PROXY, egress.EGRESS_ANTHROPIC_DOWN}
    assert len(vals) == 4


# ── deny family unchanged by egress containment ─────────────────────────────
def test_egress_containment_does_not_alter_the_deny_family():
    # Network containment ADDS a layer; it removes nothing. The credential/env/WebFetch
    # denies the settings canary proves must still be present...
    assert any("credentials.json" in d for d in DENY)
    assert "Bash(env)" in DENY and any("printenv" in d for d in DENY)
    assert any(d.startswith("Bash(curl") for d in DENY)
    assert "WebFetch" in DENY
    # ...and WebSearch is NOT denied (it never was — the reviewed "un-deny" strand was a
    # no-op against a settings.json that never contained it; nothing to give back).
    assert not any("WebSearch" in d for d in DENY)
