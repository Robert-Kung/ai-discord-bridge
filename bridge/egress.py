"""Egress containment canary (phase 1).

Proves — before the bridge serves — that the container's network posture is the
intended one, with THREE probes (the classifier is pure and unit-tested):

  1. direct connect to a non-allow-listed control host MUST fail  → the route is gone
  2. that control host attempted VIA THE PROXY MUST be denied      → the allow-list is
     really default-deny (catches an allow-all / empty-ACL proxy — the most likely
     misconfig, which a connectivity-only canary sails straight past)
  3. api.anthropic.com via the proxy MUST succeed                  → the proxy works

Only all-three-correct is OK. A reachable direct route (OPEN_EGRESS) or a proxy that
tunnels the control host (OPEN_PROXY) is a hard failure → refuse to serve. Anthropic
being unreachable (ANTHROPIC_DOWN) is transient → the caller retries with backoff.
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

from bridge import config

log = logging.getLogger("bridge.egress")

EGRESS_OK = "ok"
EGRESS_OPEN = "open_egress"        # direct route out is still present → refuse
EGRESS_OPEN_PROXY = "open_proxy"   # proxy TUNNELS a non-allow-listed host → refuse
EGRESS_INCONCLUSIVE = "inconclusive"      # can't prove default-deny → retry, never OK
EGRESS_ANTHROPIC_DOWN = "anthropic_down"  # transient → retry

# Proxy CONNECT outcomes — kept distinct so probe 2 asserts the SPECIFIC deny signal
# (403/407), not mere non-reachability. A 502/timeout means the proxy allowed the tunnel
# but the upstream was down — that is NOT proof of default-deny, so it must never read
# as "contained" (else an allow-all proxy whose control host blips passes the canary).
PROXY_TUNNELED = "tunneled"        # 2xx — CONNECT established (host is reachable via proxy)
PROXY_DENIED = "denied"            # 403/407 — proxy refused (default-deny confirmed)
PROXY_INCONCLUSIVE = "inconclusive"  # 502/504/timeout/closed — proves nothing


def classify_egress(control_direct_reachable: bool,
                    control_via_proxy: str,
                    anthropic_via_proxy: str) -> str:
    """Pure decision over the three probe outcomes. `control_via_proxy` and
    `anthropic_via_proxy` are PROXY_* statuses (not bools). Order matters: a live direct
    route is most severe (no isolation), then an allow-all proxy, then an unprovable
    default-deny, then Anthropic reachability."""
    if control_direct_reachable:
        return EGRESS_OPEN
    if control_via_proxy == PROXY_TUNNELED:
        return EGRESS_OPEN_PROXY
    if control_via_proxy != PROXY_DENIED:
        # 502/timeout to the control host: the proxy did not explicitly deny it, so we
        # cannot prove the allow-list is default-deny. Fail closed (retry), never OK.
        return EGRESS_INCONCLUSIVE
    if anthropic_via_proxy != PROXY_TUNNELED:
        return EGRESS_ANTHROPIC_DOWN
    return EGRESS_OK


async def _direct_reachable(host: str, port: int, timeout: float) -> bool:
    """True iff a raw TCP connection to host:port succeeds with NO proxy — i.e. the
    container still has a direct route out."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except (OSError, asyncio.TimeoutError):
        return False


def _classify_connect_status(status_line: str) -> str:
    """Map a proxy's CONNECT response status line to a PROXY_* outcome. 2xx = tunneled;
    an EXPLICIT 403/407 = denied (default-deny); anything else (502/504/malformed) is
    inconclusive — it does not prove the proxy would refuse a non-allow-listed host."""
    parts = status_line.split()
    code = parts[1] if len(parts) >= 2 and parts[1].isdigit() else ""
    if code.startswith("2"):
        return PROXY_TUNNELED
    if code in ("403", "407"):
        return PROXY_DENIED
    return PROXY_INCONCLUSIVE


async def _proxy_connect_status(proxy_url: str, host: str, port: int, timeout: float) -> str:
    """CONNECT to host:port through the proxy and classify the reply (PROXY_*). Proxy
    unreachable / timeout / closed → PROXY_INCONCLUSIVE (proves nothing)."""
    parsed = urlparse(proxy_url)
    phost, pport = parsed.hostname, parsed.port or 8888
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(phost, pport), timeout=timeout)
    except (OSError, asyncio.TimeoutError):
        return PROXY_INCONCLUSIVE
    try:
        writer.write(
            f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        return _classify_connect_status(line.decode("latin-1", "replace"))
    except (OSError, asyncio.TimeoutError):
        return PROXY_INCONCLUSIVE
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def run_egress_canary(timeout: float = 5.0) -> str:
    """Run the three probes against the configured proxy and classify. Requires
    config.EGRESS_PROXY_URL to be set (the caller only invokes this when it is)."""
    proxy = config.EGRESS_PROXY_URL
    control = config.EGRESS_CANARY_CONTROL_HOST
    anthropic = config.ANTHROPIC_API_HOST
    control_direct = await _direct_reachable(control, 443, timeout)
    control_via_proxy = await _proxy_connect_status(proxy, control, 443, timeout)
    anthropic_via_proxy = await _proxy_connect_status(proxy, anthropic, 443, timeout)
    status = classify_egress(control_direct, control_via_proxy, anthropic_via_proxy)
    log.info("egress canary: direct(%s)=%s proxy(%s)=%s proxy(%s)=%s → %s",
             control, control_direct, control, control_via_proxy,
             anthropic, anthropic_via_proxy, status)
    return status
