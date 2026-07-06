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
EGRESS_OPEN_PROXY = "open_proxy"   # proxy tunnels a non-allow-listed host → refuse
EGRESS_ANTHROPIC_DOWN = "anthropic_down"  # transient → retry


def classify_egress(control_direct_reachable: bool,
                    control_via_proxy_reachable: bool,
                    anthropic_via_proxy_reachable: bool) -> str:
    """Pure decision over the three probe outcomes. Order matters: a live direct route
    is the most severe (no isolation at all), then an allow-all proxy, then Anthropic
    reachability (the only transient/retryable one)."""
    if control_direct_reachable:
        return EGRESS_OPEN
    if control_via_proxy_reachable:
        return EGRESS_OPEN_PROXY
    if not anthropic_via_proxy_reachable:
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


async def _proxy_connect_ok(proxy_url: str, host: str, port: int, timeout: float) -> bool:
    """True iff an HTTP CONNECT tunnel to host:port is established through the proxy
    (proxy replies 2xx). A 403/deny (default-deny allow-list) → False."""
    parsed = urlparse(proxy_url)
    phost, pport = parsed.hostname, parsed.port or 8888
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(phost, pport), timeout=timeout)
    except (OSError, asyncio.TimeoutError):
        # proxy itself unreachable — treat as "not reachable via proxy"
        return False
    try:
        writer.write(
            f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        status = line.decode("latin-1", "replace")
        # "HTTP/1.1 200 Connection established" = tunnel open; 403/407/502 = denied/blocked
        return " 200 " in status or status.rstrip().endswith(" 200")
    except (OSError, asyncio.TimeoutError):
        return False
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
    control_via_proxy = await _proxy_connect_ok(proxy, control, 443, timeout)
    anthropic_via_proxy = await _proxy_connect_ok(proxy, anthropic, 443, timeout)
    status = classify_egress(control_direct, control_via_proxy, anthropic_via_proxy)
    log.info("egress canary: direct(%s)=%s proxy(%s)=%s proxy(%s)=%s → %s",
             control, control_direct, control, control_via_proxy,
             anthropic, anthropic_via_proxy, status)
    return status
