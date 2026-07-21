"""Trust filtering + execution-tier gates + the !cd project guard.

These are the security seams: who may influence a bot's context (`_is_trusted`),
which opt-in execution tiers are reachable (`bypass_allowed`/`approve_allowed`/
`_tier_allowed`), and which cwd targets `!cd` accepts (`resolve_project_cwd`).
"""
from __future__ import annotations

from pathlib import Path

from bridge import config, state


def _is_trusted(m: dict) -> bool:
    """A2a injection isolation: only whitelisted humans and our OWN A/B bots may
    influence what a bot sees (context) or what gets summarised (flush). A random
    channel member's text — or any THIRD-PARTY bot/webhook — is dropped so it can't
    smuggle instructions into a call a whitelisted user later triggers.

    `m["bot"]` alone is NOT enough: it's true for every Discord bot, so we match the
    author id against our own bots (recorded replies carry author_id None and are ours)."""
    aid = m.get("author_id")
    if aid is None:
        return bool(m.get("bot"))  # our own recorded reply
    return aid in config.ALLOWED_USER_IDS or aid in set(state.bot_user_ids.values())


def resolve_project_cwd(raw: str) -> tuple[str | None, str]:
    """Validate a !cd target. Returns (resolved_path_or_None, message).

    Accepts full path or bare project name. Rejects anything outside the whitelist
    or lacking a .git dir (git-only guard)."""
    raw = raw.strip()
    if not raw:
        return None, "empty"
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = config.PROJECT_BASE_DIR / raw  # bare name → <base>/<name>
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError):
        return None, f"無法解析路徑：{raw}"
    in_whitelist = any(
        resolved == p or resolved.is_relative_to(p) for p in config.PROJECT_DIRS
    )
    if not in_whitelist:
        return None, f"🛡 `{resolved}` 不在專案白名單內"
    if not (resolved / ".git").is_dir():
        return None, f"🛡 `{resolved}` 不是 git 專案（缺 .git）"
    return str(resolved), "ok"


def bypass_allowed(author_id: int) -> bool:
    """Full bypass is reachable only when the opt-in tier is enabled AND the user is
    whitelisted (OV4 / 3.2). Default-closed: tier off → never reachable, by anyone."""
    return config.BYPASS_TIER_ENABLED and author_id in config.ALLOWED_USER_IDS


def approve_allowed(author_id: int) -> bool:
    """The M4 per-command approve tier is reachable only when ENABLE_APPROVER_TIER is on
    AND the user is whitelisted. Default-closed, same shape as bypass_allowed."""
    return config.APPROVER_TIER_ENABLED and author_id in config.ALLOWED_USER_IDS


def _tier_allowed(mode: str, author_id: int) -> bool:
    """Whether an opt-in execution tier is currently reachable for this user. plan/edit
    are always permitted here (edit's whitelist is enforced upstream); bypass/approve are
    the default-closed opt-in tiers."""
    if mode == "bypass":
        return bypass_allowed(author_id)
    if mode == "approve":
        return approve_allowed(author_id)
    return True
