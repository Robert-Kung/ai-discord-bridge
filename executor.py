"""Executor container entrypoint (egress-exec-isolation phase 2).

The ONLY process that spawns `claude -p`. Holds the Claude credentials and an
Anthropic-only egress posture; sees no Discord token, no channel id, no user list
(load_executor_config never reads them). Serves semantic run-requests from the
discord-frontend over a unix socket on the shared discord-state volume — the
frontend cannot submit argv or env, only whitelisted parameter combinations
(runner._validate_exec_request).

Startup order mirrors bot.py's fail-closed discipline:
  1. executor config load (no Discord material)
  2. API-key mode: provision per-bot apiKeyHelper (3.x)
  3. egress canary — this container's OWN deny direction (5.3): api.anthropic.com
     required, every Discord host explicitly denied
  4. serve the socket
"""
from __future__ import annotations

import asyncio
import logging
import os

from bridge import config, egress, runner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("executor")


async def main() -> None:
    config.load_executor_config()
    if not config.EXECUTOR_SOCKET:
        raise SystemExit("executor requires EXECUTOR_SOCKET (shared-volume socket path)")
    if config.USE_API_KEY:
        for cfg in config.BOTS.values():
            runner.provision_api_key_helper(cfg)
    # Forbidden set = Discord (the peer's credential lives there) + the package-upload
    # endpoints, so the opt-in's core invariant — read hosts only, never a write path —
    # is proven at startup instead of assumed from the filter file.
    await egress.canary_gate(required_host=config.ANTHROPIC_API_HOST,
                             forbidden_hosts=config.EXECUTOR_FORBIDDEN_HOSTS)
    # The executor is where claude actually spawns, so it owns the settings-canary proof
    # of the deny family (the frontend can't — no credentials / no Anthropic egress).
    if os.environ.get("BRIDGE_SKIP_CANARY", "").strip().lower() not in ("1", "true", "yes", "on"):
        await runner.settings_canary_gate()
    # M4 tier (verify + exec-tier Bash) is LIVE here only when opted in — the phase-2
    # posture is proven by the Discord-deny egress canary above. Generate the
    # Bash-permitting exec settings and prove they STILL deny (claude silently ignores
    # an invalid --settings file; without this the base canary would pass while the
    # Bash-job settings dropped every deny). Fail-closed on a dropped deny.
    if runner.m4_live():
        runner.write_exec_settings()
        skip = os.environ.get("BRIDGE_SKIP_CANARY", "").strip().lower() in ("1", "true", "yes", "on")
        if not skip:
            status = await runner.run_settings_canary(settings_path=config.EXEC_SETTINGS_PATH)
            if status == runner.CANARY_DENY_DROPPED:
                raise SystemExit(
                    "exec-tier settings canary FAILED — the Bash-permitting exec settings "
                    "loaded but the deny family did NOT fire. Refusing to serve the M4 tier.")
            if status != runner.CANARY_OK:
                log.warning("exec-tier settings canary inconclusive (%s) — proceeding; the "
                            "base canary already proved the deny family loads.", status)
        log.info("M4 tier LIVE: post-task verify + exec-tier Bash enabled (executor)")
    await runner.serve_executor()


if __name__ == "__main__":
    asyncio.run(main())
