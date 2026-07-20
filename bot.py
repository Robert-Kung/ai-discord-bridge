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

from bridge import config, egress, jobs, worktree
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
    # Recover exec jobs from the previous container: mark orphaned running jobs, reload
    # awaiting-review jobs into the registry (so !merge/!discard keep working), then GC
    # stale bridge/<id> worktrees + branches — keeping only the awaiting-review ones.
    _, _orphans = jobs.recover_jobs()
    # An orphaned job whose branch moved past base holds committed work (an agent
    # commits per task during long runs) — park it for !merge/!discard BEFORE the GC
    # below (which computes _keep after this) can delete the branch.
    await jobs.rescue_committed_orphans(_orphans)
    _keep = jobs.awaiting_review_ids_by_project()
    for _project in {str(p) for p in config.PROJECT_DIRS}:
        try:
            await worktree.gc_project(_project, _keep.get(_project, set()))
        except Exception:
            log.exception("startup GC failed for %s", _project)
    # Clean up on-disk job state (mirrors + attachment/diff dirs) for finished jobs,
    # keeping only the awaiting-review ones — else uploaded content accumulates forever.
    jobs.gc_job_state({jid for ids in _keep.values() for jid in ids})

    # API-key mode (egress-exec-isolation 3.x): materialize each bot's key as a
    # config-dir file + apiKeyHelper BEFORE any claude call (the settings canary below
    # is one). Subscription mode: skipped entirely — the OAuth path is never touched.
    # Split deploy: the EXECUTOR provisions (it owns the config dirs + spawns claude);
    # the frontend must not — it has no key material at all.
    if config.USE_API_KEY and not config.EXECUTOR_SOCKET:
        for _cfg in config.BOTS.values():
            runner.provision_api_key_helper(_cfg)

    # Fail-closed startup assertion: a dead approve tier (script missing) would only
    # surface the first time someone uses `!mode approve`; the default-mode canary
    # cannot catch it, so assert here.
    if config.APPROVER_TIER_ENABLED and not Path(config.APPROVER_SCRIPT).exists():
        raise SystemExit(
            f"ENABLE_APPROVER_TIER is set but the approver script is missing: "
            f"{config.APPROVER_SCRIPT}. Refusing to start with a dead approve tier."
        )

    # Egress containment: prove the network posture before anything else (fail-closed;
    # skipped when no proxy is configured). Split deploy (EXECUTOR_SOCKET set): this
    # process is the DISCORD FRONTEND — its proxy must reach Discord and must NOT reach
    # Anthropic (5.3 deny direction; the executor asserts the mirror image). Single
    # container: the phase-1 posture (Anthropic required, no forbidden set).
    if config.EXECUTOR_SOCKET:
        await egress.canary_gate(required_host="discord.com",
                                 forbidden_hosts=(config.ANTHROPIC_API_HOST,))
    else:
        await egress.canary_gate()

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
    # The settings canary spawns claude LOCALLY. In a split deploy the frontend has no
    # credentials and no Anthropic egress, so it must NOT run the canary here — the
    # executor proves the deny family on its side (executor.py). Single container: run it.
    if not skip_canary and not config.EXECUTOR_SOCKET:
        await runner.settings_canary_gate()

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
