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
    await egress.canary_gate(required_host=config.ANTHROPIC_API_HOST,
                             forbidden_hosts=config.DISCORD_HOSTS)
    await runner.serve_executor()


if __name__ == "__main__":
    asyncio.run(main())
