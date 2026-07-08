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
EGRESS_ANTHROPIC_DOWN = "anthropic_down"  # required host unreachable — transient → retry
# Phase 2 (5.3): a host belonging to the OTHER container's egress set is tunnelable from
# here — the split's core guarantee is broken (e.g. Discord reachable from the executor,
# where the credential lives) → refuse to serve, same severity as OPEN_PROXY.
EGRESS_PEER_REACHABLE = "peer_reachable"

# Proxy CONNECT outcomes — kept distinct so probe 2 asserts the SPECIFIC deny signal
# (403/407), not mere non-reachability. A 502/timeout means the proxy allowed the tunnel
# but the upstream was down — that is NOT proof of default-deny, so it must never read
# as "contained" (else an allow-all proxy whose control host blips passes the canary).
PROXY_TUNNELED = "tunneled"        # 2xx — CONNECT established (host is reachable via proxy)
PROXY_DENIED = "denied"            # 403/407 — proxy refused (default-deny confirmed)
PROXY_INCONCLUSIVE = "inconclusive"  # 502/504/timeout/closed — proves nothing


def classify_egress(control_direct_reachable: bool,
                    control_via_proxy: str,
                    anthropic_via_proxy: str,
                    forbidden_via_proxy: "tuple[str, ...]" = ()) -> str:
    """Pure decision over the probe outcomes. `control_via_proxy`, `anthropic_via_proxy`
    (the REQUIRED host) and each `forbidden_via_proxy` entry are PROXY_* statuses (not
    bools). Order matters: a live direct route is most severe (no isolation), then an
    allow-all proxy, then a reachable peer-container host (5.3 — the split's own deny
    direction), then an unprovable default-deny, then required-host reachability."""
    if control_direct_reachable:
        return EGRESS_OPEN
    if control_via_proxy == PROXY_TUNNELED:
        return EGRESS_OPEN_PROXY
    for status in forbidden_via_proxy:
        if status == PROXY_TUNNELED:
            return EGRESS_PEER_REACHABLE
    if control_via_proxy != PROXY_DENIED:
        # 502/timeout to the control host: the proxy did not explicitly deny it, so we
        # cannot prove the allow-list is default-deny. Fail closed (retry), never OK.
        return EGRESS_INCONCLUSIVE
    for status in forbidden_via_proxy:
        if status != PROXY_DENIED:
            # can't prove the peer host is denied either → not containment yet
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


async def run_egress_canary(timeout: float = 5.0,
                            required_host: "str | None" = None,
                            forbidden_hosts: "tuple[str, ...]" = ()) -> str:
    """Run the probes against the configured proxy and classify. Requires
    config.EGRESS_PROXY_URL to be set (the caller only invokes this when it is).
    `required_host` defaults to the Anthropic API host (phase-1 / executor posture);
    `forbidden_hosts` is the peer container's egress set (phase 2, 5.3) — each must be
    explicitly DENIED by this container's proxy."""
    proxy = config.EGRESS_PROXY_URL
    control = config.EGRESS_CANARY_CONTROL_HOST
    required = required_host or config.ANTHROPIC_API_HOST
    control_direct = await _direct_reachable(control, 443, timeout)
    control_via_proxy = await _proxy_connect_status(proxy, control, 443, timeout)
    required_via_proxy = await _proxy_connect_status(proxy, required, 443, timeout)
    forbidden_via_proxy = tuple(
        [await _proxy_connect_status(proxy, h, 443, timeout) for h in forbidden_hosts])
    status = classify_egress(control_direct, control_via_proxy, required_via_proxy,
                             forbidden_via_proxy)
    log.info("egress canary: direct(%s)=%s proxy(%s)=%s proxy(%s)=%s forbidden=%s → %s",
             control, control_direct, control, control_via_proxy,
             required, required_via_proxy,
             dict(zip(forbidden_hosts, forbidden_via_proxy)), status)
    return status


async def canary_gate(required_host: "str | None" = None,
                      forbidden_hosts: "tuple[str, ...]" = ()) -> None:
    """Shared fail-closed startup gate (bot.py frontend + executor.py): loop the canary
    with backoff; a posture violation (OPEN_EGRESS / OPEN_PROXY / PEER_REACHABLE) is a
    hard SystemExit, anything transient retries in-process (never a container crash-loop
    — the canary_oauth_crashloop lesson). No-op when no proxy is configured."""
    if not config.EGRESS_PROXY_URL:
        return
    backoff = config.CANARY_RETRY_BASE
    attempts = 0
    while True:
        status = await run_egress_canary(
            required_host=required_host, forbidden_hosts=forbidden_hosts)
        if status == EGRESS_OK:
            log.info("egress canary passed (required=%s forbidden=%s proxy=%s)",
                     required_host or config.ANTHROPIC_API_HOST, list(forbidden_hosts),
                     config.EGRESS_PROXY_URL)
            return
        if status in (EGRESS_OPEN, EGRESS_OPEN_PROXY, EGRESS_PEER_REACHABLE):
            detail = {
                EGRESS_OPEN: "a direct route out still exists (internal network not in force)",
                EGRESS_OPEN_PROXY: "the proxy tunnels a non-allow-listed host (not default-deny)",
                EGRESS_PEER_REACHABLE: "a peer container's host is reachable from HERE — the "
                                       "split's deny direction is broken",
            }[status]
            raise SystemExit(
                f"egress canary failed ({status}) — {detail}. Refusing to serve so a "
                "bypassed agent can't exfiltrate.")
        # transient (proxy starting) vs permanent (allow-list typo): after several
        # failures escalate the message instead of looping silently on a lie
        # (the canary_oauth_crashloop diagnosability lesson).
        attempts += 1
        if attempts >= 5:
            hint = (f"check the proxy allow-list includes {required_host or config.ANTHROPIC_API_HOST}"
                    if status == EGRESS_ANTHROPIC_DOWN
                    else "the control/forbidden host got no explicit deny — check the proxy "
                         "is up and EGRESS_CANARY_CONTROL_HOST resolves")
            log.error("egress canary still failing after %d attempts (status=%s) — likely "
                      "a PERMANENT misconfig, not transient: %s. Still retrying in %ds "
                      "(fail-closed, not serving).", attempts, status, hint, backoff)
        else:
            log.error("egress canary not yet green (status=%s; proxy still starting?). "
                      "Retrying in %ds (in-process, fail-closed).", status, backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, config.CANARY_RETRY_MAX)
