"""Egress canary classification (spec: egress-containment).

The three-probe classifier must fail closed on BOTH a live direct route and an
allow-all proxy — the latter is the misconfig a connectivity-only canary misses.
"""
import json
from pathlib import Path

from bridge import config, egress

REPO = Path(config.__file__).resolve().parent.parent
DENY = json.loads((REPO / "settings.json").read_text())["permissions"]["deny"]


D = egress.PROXY_DENIED
T = egress.PROXY_TUNNELED
I = egress.PROXY_INCONCLUSIVE


def test_ok_only_when_contained_and_anthropic_up():
    # direct fails, proxy DENIES control (explicit 403), proxy tunnels anthropic → contained
    assert egress.classify_egress(False, D, T) == egress.EGRESS_OK


def test_direct_route_present_refuses():
    # a live direct route means no isolation at all — most severe, checked first
    assert egress.classify_egress(True, D, T) == egress.EGRESS_OPEN
    assert egress.classify_egress(True, T, T) == egress.EGRESS_OPEN


def test_allow_all_proxy_refuses():
    # THE key case: direct route gone, but the proxy TUNNELS the control host
    # (empty/broken ACL). A connectivity-only canary would pass this; we must not.
    assert egress.classify_egress(False, T, T) == egress.EGRESS_OPEN_PROXY
    assert egress.classify_egress(False, T, I) == egress.EGRESS_OPEN_PROXY


def test_inconclusive_control_probe_never_reads_as_contained():
    # 502/timeout to the control host via the proxy does NOT prove default-deny — an
    # allow-all proxy whose upstream blipped would land here. Must fail closed (retry),
    # never OK, even when anthropic is reachable.
    assert egress.classify_egress(False, I, T) == egress.EGRESS_INCONCLUSIVE


def test_anthropic_down_is_transient():
    # contained (control denied), but the proxy can't tunnel anthropic → retryable
    assert egress.classify_egress(False, D, I) == egress.EGRESS_ANTHROPIC_DOWN
    assert egress.classify_egress(False, D, egress.PROXY_DENIED) == egress.EGRESS_ANTHROPIC_DOWN


def test_connect_status_parsing():
    # the exact parsing probe 2's soundness turns on
    assert egress._classify_connect_status("HTTP/1.1 200 Connection established") == T
    assert egress._classify_connect_status("HTTP/1.0 200 OK") == T
    assert egress._classify_connect_status("HTTP/1.1 403 Filtered") == D
    assert egress._classify_connect_status("HTTP/1.1 407 Proxy Authentication Required") == D
    assert egress._classify_connect_status("HTTP/1.1 502 Bad Gateway") == I
    assert egress._classify_connect_status("HTTP/1.1 504 Gateway Timeout") == I
    assert egress._classify_connect_status("") == I
    assert egress._classify_connect_status("garbage") == I


def test_status_constants_distinct():
    vals = {egress.EGRESS_OK, egress.EGRESS_OPEN, egress.EGRESS_OPEN_PROXY,
            egress.EGRESS_INCONCLUSIVE, egress.EGRESS_ANTHROPIC_DOWN}
    assert len(vals) == 5


# ── deny family unchanged by egress containment ─────────────────────────────
def test_egress_containment_does_not_alter_the_deny_family():
    # Network containment ADDS a layer; it removes nothing. The credential/env/WebFetch
    # denies the settings canary proves must still be present...
    assert any("credentials.json" in d for d in DENY)
    assert "Bash(env)" in DENY and any("printenv" in d for d in DENY)
    assert any(d.startswith("Bash(curl") for d in DENY)
    # egress-allowlist-a (2026-07-20): WebFetch left the blanket deny for a
    # domain-scoped allow (proxy filter is the enforcement); it must never come
    # back as a blanket ALLOW either — that's covered in test_permissions.
    assert "WebFetch" not in DENY
    assert not any("WebSearch" in d for d in DENY)
