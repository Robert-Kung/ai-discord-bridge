"""Channel-state and per-(bot, cwd) session-id persistence (leaf over config).

Claude Code stores sessions per project dir (cwd). A session created at /home/user
can't be --resume'd from /home/user/my-project, so the session id is keyed by
(bot, cwd), not just bot.
"""
from __future__ import annotations

import json
from pathlib import Path

from bridge import config


# ── Channel state file persistence ──────────────────────────────────────
def channel_state_path(channel_id: int) -> Path:
    return config.STATE_DIR / f"channel_{channel_id}.json"


def load_channel_state(channel_id: int) -> dict:
    p = channel_state_path(channel_id)
    if not p.exists():
        return {"mode": config.DEFAULT_CHANNEL_MODE}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {"mode": config.DEFAULT_CHANNEL_MODE}


def save_channel_state(channel_id: int, state: dict) -> None:
    p = channel_state_path(channel_id)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    tmp.replace(p)


def get_channel_cwd(channel_id: int) -> str:
    """Current working dir for this channel (validated; falls back to default)."""
    cwd = load_channel_state(channel_id).get("cwd")
    if cwd and Path(cwd).is_dir():
        return cwd
    return config.DEFAULT_CWD


# ── Per-(bot, cwd) session id persistence ───────────────────────────────
def _cwd_slug(cwd: str) -> str:
    return cwd.strip("/").replace("/", "-") or "root"


def _session_path(bot_name: str, cwd: str) -> Path:
    return config.STATE_DIR / f"{bot_name}__{_cwd_slug(cwd)}.json"


def load_session(bot_name: str, cwd: str | None = None) -> str | None:
    cwd = config.DEFAULT_CWD if cwd is None else cwd
    p = _session_path(bot_name, cwd)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text()).get("session_id")
    except (json.JSONDecodeError, OSError):
        return None


def save_session(bot_name: str, sid: str, cwd: str | None = None) -> None:
    cwd = config.DEFAULT_CWD if cwd is None else cwd
    p = _session_path(bot_name, cwd)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"session_id": sid, "cwd": cwd}))
    tmp.replace(p)


def clear_session(bot_name: str, cwd: str | None = None) -> None:
    cwd = config.DEFAULT_CWD if cwd is None else cwd
    _session_path(bot_name, cwd).unlink(missing_ok=True)
