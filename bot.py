"""Discord ↔ dual-account Claude Code bridge — entrypoint.

The bridge is a package (`bridge/`); this file is the thin `main()` wiring only. See
bridge/frontend.py for the Discord surface and bridge/runner.py for the execution
chokepoint. `python bot.py` still runs the bot.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from bridge import config, egress
from bridge.approver_ipc import start_approval_server
from bridge.frontend import make_client, request_discord_approval
from bridge import runner, state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("bridge")


async def main():
    config.load_config()  # read env into globals + ensure dirs + fail-closed validation

    # Fail-closed startup assertion: a dead approve tier (script missing) would only
    # surface the first time someone uses `!mode approve`; the default-mode canary
    # cannot catch it, so assert here.
    if config.APPROVER_TIER_ENABLED and not Path(config.APPROVER_SCRIPT).exists():
        raise SystemExit(
            f"ENABLE_APPROVER_TIER is set but the approver script is missing: "
            f"{config.APPROVER_SCRIPT}. Refusing to start with a dead approve tier."
        )

    # Egress containment (phase 1): when a proxy is configured, prove the network
    # posture before anything else — a live direct route (OPEN_EGRESS) or an allow-all
    # proxy (OPEN_PROXY) is a hard failure (refuse to serve); Anthropic unreachable is
    # transient (backoff retry, same shape as the settings canary's CANNOT_RUN). When no
    # proxy is configured (uncontained single-container deploy) this is skipped.
    if config.EGRESS_PROXY_URL:
        backoff = config.CANARY_RETRY_BASE
        attempts = 0
        while True:
            status = await egress.run_egress_canary()
            if status == egress.EGRESS_OK:
                log.info("egress canary passed: network egress is contained (proxy=%s)",
                         config.EGRESS_PROXY_URL)
                break
            if status in (egress.EGRESS_OPEN, egress.EGRESS_OPEN_PROXY):
                raise SystemExit(
                    f"egress canary failed ({status}) — the container's network posture is "
                    "wrong: a non-allow-listed control host is reachable "
                    f"({'directly — the internal network still has a route out' if status == egress.EGRESS_OPEN else 'via the proxy — the allow-list is not default-deny'}). "
                    "Refusing to serve so a bypassed agent can't exfiltrate."
                )
            # ANTHROPIC_DOWN / INCONCLUSIVE → fail-closed retry (proxy still starting, or a
            # transient blip). After several failures it is probably NOT transient — a
            # permanent allow-list typo (missing api.anthropic.com, or the control host
            # unreachable via the proxy) — so escalate the message instead of looping
            # silently on a lie (the canary_oauth_crashloop diagnosability lesson).
            attempts += 1
            if attempts >= 5:
                hint = ("check egress-proxy/filter includes api.anthropic.com"
                        if status == egress.EGRESS_ANTHROPIC_DOWN
                        else "the control host is not reachable via the proxy — check the "
                             "proxy is up and EGRESS_CANARY_CONTROL_HOST resolves")
                log.error("egress canary still failing after %d attempts (status=%s) — this "
                          "is likely a PERMANENT misconfig, not transient: %s. Still retrying "
                          "in %ds (fail-closed, not serving).", attempts, status, hint, backoff)
            else:
                log.error("egress canary not yet green (status=%s; proxy still starting?). "
                          "Retrying in %ds (in-process, fail-closed).", status, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, config.CANARY_RETRY_MAX)

    # OV1 — prove the --settings deny family is actually in force before serving.
    skip_canary = os.environ.get("BRIDGE_SKIP_CANARY", "").strip().lower() in ("1", "true", "yes", "on")
    if skip_canary and config.BYPASS_TIER_ENABLED:
        raise SystemExit(
            "BRIDGE_SKIP_CANARY and ENABLE_BYPASS_TIER are both set — refusing to start. "
            "The canary is the only proof the deny family loaded; skipping it while the "
            "full-bypass tier is enabled would serve execution with unverified deny rules."
        )
    if skip_canary:
        log.warning("BRIDGE_SKIP_CANARY set — starting WITHOUT proving the deny family "
                    "loaded. Offline-dev only; never use this in a real deployment.")
    if not skip_canary:
        # OK → proceed; DENY_DROPPED → refuse (config bug, retry won't fix);
        # CANNOT_RUN → in-process backoff retry (auth lapse; SystemExit would crash-loop).
        backoff = config.CANARY_RETRY_BASE
        while True:
            status = await runner.run_settings_canary()
            if status == runner.CANARY_OK:
                log.info("settings canary passed: --settings deny family is in force")
                break
            if status == runner.CANARY_DENY_DROPPED:
                raise SystemExit(
                    "settings canary failed — claude ran but the must-be-denied action "
                    "was NOT denied: the --settings permissions.deny family is not in "
                    "force (schema drift / unloadable settings). Refusing to start. "
                    "Set BRIDGE_SKIP_CANARY=1 only for offline dev."
                )
            log.error("settings canary could not run — claude is unavailable / not logged "
                      "in. The bot will NOT serve until auth recovers; retrying in %ds "
                      "(in-process, no restart loop).", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, config.CANARY_RETRY_MAX)

    for name in config.BOTS:
        state.clients[name] = make_client(name)
    if config.APPROVER_TIER_ENABLED:
        await start_approval_server(request_discord_approval)  # M4 per-command approval socket
    log.info("starting bridge v3: channel=%d allowed=%s max_turns=%d auto_flush=%d "
             "bypass_tier=%s approver_tier=%s",
             config.CHANNEL_ID, sorted(config.ALLOWED_USER_IDS), config.MAX_BOT_TURNS,
             config.AUTO_FLUSH_THRESHOLD, config.BYPASS_TIER_ENABLED, config.APPROVER_TIER_ENABLED)
    await asyncio.gather(*(state.clients[n].start(config.BOTS[n]["token"]) for n in config.BOTS))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("shutdown")
