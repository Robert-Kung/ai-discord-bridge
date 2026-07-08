"""Configuration + fail-closed validation for the bridge (leaf module).

Import side-effect-free: every env-derived value stays at its fail-closed default
until `load_config()` runs (at startup, or in tests after monkeypatching os.environ).
Consumers MUST read these as attributes (`config.CHANNEL_ID`), never via
`from bridge.config import CHANNEL_ID` — a from-import captures the pre-load default
permanently and yields a silently-dead consumer (fail-closed with a green canary).
An AST test (tests/test_boundaries.py) enforces this.
"""
from __future__ import annotations

import os
from pathlib import Path

# Repo root: this file is <repo>/bridge/config.py, so parent.parent is the repo.
# Anchoring on the repo (not __file__ of this module) keeps settings.json /
# mcp_approver.py / approver-allowlist.json resolvable after the package split.
_REPO_ROOT = Path(__file__).resolve().parent.parent

# ── Paths ───────────────────────────────────────────────────────────────
SHARED_DIR = Path("/home/user/.claude-shared")
STATE_DIR = SHARED_DIR / "discord-state"
SUMMARIES_DIR = SHARED_DIR / "discord-summaries"
# 專案層記憶：放 rw 的 .claude-shared 下、但不在容器內 ro 掛載的 memory/ → bot 可寫
PROJECT_NOTES_DIR = SHARED_DIR / "discord-project-notes"
CSWAP_USAGE_FILE = STATE_DIR / "cswap-usage.json"  # written by host cron, read here

# ── Thresholds (env defaults at import; no crash; referenced by HELP_TEXT etc.) ──
MAX_BOT_TURNS = int(os.environ.get("MAX_BOT_TURNS", "6"))
CLAUDE_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT", "300"))
# Execution-tier background jobs (agent-exec-loop M1) get a much larger timeout than a
# conversation call, run as tracked background jobs, and stream progress. Distinct from
# CLAUDE_TIMEOUT so a long edit task never inherits the short conversation kill-timer.
EXEC_TIMEOUT = int(os.environ.get("EXEC_TIMEOUT", "1800"))
# Status-message edit throttle (s) and the rolling tool-use trace depth.
EXEC_STATUS_EDIT_INTERVAL = float(os.environ.get("EXEC_STATUS_EDIT_INTERVAL", "2.0"))
EXEC_TRACE_LINES = int(os.environ.get("EXEC_TRACE_LINES", "12"))
# Attachment ingestion (M3): per-message count cap, per-file size cap, aggregate byte
# budget across one message, and a wall-clock cap on the whole download step.
EXEC_ATTACH_MAX_COUNT = int(os.environ.get("EXEC_ATTACH_MAX_COUNT", "5"))
EXEC_ATTACH_MAX_BYTES = int(os.environ.get("EXEC_ATTACH_MAX_BYTES", str(10 * 1024 * 1024)))
EXEC_ATTACH_MAX_TOTAL_BYTES = int(os.environ.get("EXEC_ATTACH_MAX_TOTAL_BYTES", str(25 * 1024 * 1024)))
EXEC_ATTACH_TIMEOUT = int(os.environ.get("EXEC_ATTACH_TIMEOUT", "60"))
AUTO_FLUSH_THRESHOLD = int(os.environ.get("AUTO_FLUSH_THRESHOLD", "20"))
# Startup-canary retry backoff (s) for the "claude can't run / not logged in" case.
# We wait-and-retry IN-PROCESS instead of letting a SystemExit hand docker a tight
# crash-loop (the OAuth-expiry incident restarted the container 188 times).
CANARY_RETRY_BASE = int(os.environ.get("CANARY_RETRY_BASE", "15"))
CANARY_RETRY_MAX = int(os.environ.get("CANARY_RETRY_MAX", "300"))
# token-based flush — TWO stages, tuned for the opus[1m] 1M context window:
#  • FLUSH_TOKEN_THRESHOLD (400k): write a summary checkpoint, KEEP the session.
#  • RESET_TOKEN_THRESHOLD (700k): write a fresh summary AND reset the session.
# Set either to 0 to disable that stage.
FLUSH_TOKEN_THRESHOLD = int(os.environ.get("FLUSH_TOKEN_THRESHOLD", "400000"))
RESET_TOKEN_THRESHOLD = int(os.environ.get("RESET_TOKEN_THRESHOLD", "700000"))
# emergency hard cap: if the summariser keeps failing near the 1M ceiling, force
# a reset (losing context) rather than let the session grow until calls error out
HARD_RESET_TOKEN_THRESHOLD = int(os.environ.get("HARD_RESET_TOKEN_THRESHOLD", "900000"))
if FLUSH_TOKEN_THRESHOLD and RESET_TOKEN_THRESHOLD and RESET_TOKEN_THRESHOLD < FLUSH_TOKEN_THRESHOLD:
    import logging
    logging.getLogger("bridge.config").warning(
        "RESET_TOKEN_THRESHOLD < FLUSH; disabling checkpoint stage to avoid nonsense ordering")
    FLUSH_TOKEN_THRESHOLD = 0
PLAN_REACTION_TIMEOUT = int(os.environ.get("PLAN_REACTION_TIMEOUT", "300"))

# ── Egress containment (phase 1) ────────────────────────────────────────
# When EGRESS_PROXY_URL is set the bridge runs behind a default-deny CONNECT proxy on
# an internal (routeless) network: the bridge process reaches Discord via discord.py's
# proxy= kwarg, and `claude -p` reaches Anthropic via HTTPS_PROXY. Unset → no proxy
# (single-container non-contained deploy; the egress canary is skipped). The startup
# egress canary proves containment fail-closed before serving. See egress-containment.
EGRESS_PROXY_URL = os.environ.get("EGRESS_PROXY_URL") or None
# A host that MUST be unreachable (direct and via the proxy) if containment holds.
EGRESS_CANARY_CONTROL_HOST = os.environ.get("EGRESS_CANARY_CONTROL_HOST", "example.com")
ANTHROPIC_API_HOST = "api.anthropic.com"

# Static (env-free) bot identity → config dir. Dedicated MINIMAL config dirs (D4):
# the bots do NOT run under the operator's own ~/.claude / ~/.claude-b account dirs.
BOT_CONFIG_DIRS = {"A": "/home/user/.claude-bot-a", "B": "/home/user/.claude-bot-b"}

# Repo-tracked server-side security settings (permissions.deny family). Passed via
# --settings on EVERY claude -p call. In the container this is the bind-mounted
# ./settings.json; for host-direct runs it falls back to the repo copy.
BRIDGE_SETTINGS_PATH = os.environ.get(
    "BRIDGE_SETTINGS_PATH",
    "/home/user/.claude-bridge-settings.json"
    if Path("/home/user/.claude-bridge-settings.json").exists()
    else str(_REPO_ROOT / "settings.json"),
)

# M4 — optional per-command MCP approver tier (off by default).
APPROVER_SCRIPT = str(_REPO_ROOT / "mcp_approver.py")
APPROVER_ALLOWLIST_PATH = os.environ.get("APPROVER_ALLOWLIST", str(_REPO_ROOT / "approver-allowlist.json"))
APPROVER_SOCKET_PATH = os.environ.get("APPROVER_SOCKET_PATH", "/tmp/ai-discord-bridge-approver.sock")
APPROVER_MCP_CONFIG_PATH = "/tmp/ai-discord-bridge-approver-mcp.json"  # written at startup


def approver_socket_timeout() -> int:
    """The approver's socket read timeout. Must be LONGER than the human reaction
    window so the approver doesn't give up before the human decides."""
    return PLAN_REACTION_TIMEOUT + 15


def approve_call_timeout() -> int:
    """The `claude -p` subprocess timeout for the approve tier — must outlive the whole
    approval round-trip. Nesting: PLAN_REACTION_TIMEOUT < approver_socket_timeout()
    < approve_call_timeout()."""
    return CLAUDE_TIMEOUT + PLAN_REACTION_TIMEOUT + 60


# ── Auth mode + env-derived globals (populated by load_config) ───────────
CHANNEL_ID: int | None = None
ALLOWED_USER_IDS: set[int] = set()
USE_API_KEY: bool = False
BOTS: dict[str, dict] = {}

# Env vars the claude subprocess must never inherit (Discord tokens + the whole
# auth/billing-routing family). API-key mode re-injects ONLY the canonical per-bot
# ANTHROPIC_API_KEY (in build_subprocess_env, runner).
_SUBPROCESS_ENV_DENY = {
    "DISCORD_BOT_A_TOKEN", "DISCORD_BOT_B_TOKEN",
    "ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY_A", "ANTHROPIC_API_KEY_B",
    "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
}

# Project cwd whitelist (resolved abs paths), populated by load_config from PROJECT_DIRS.
DEFAULT_CWD = "/home/user"
PROJECT_DIRS: list[Path] = []

VALID_MODES = {"plan", "edit", "bypass", "approve"}
# "approve" is the M4 per-command tier: runs claude in `default` permission mode
# (the only mode --permission-prompt-tool is consulted in).
MODE_ALIASES = {
    "plan": "plan",
    "edit": "acceptEdits",
    "acceptedits": "acceptEdits",
    "bypass": "bypassPermissions",
    "bypasspermissions": "bypassPermissions",
    "approve": "default",
}
DEFAULT_CHANNEL_MODE = "plan"  # safe default; bypass requires opt-in

# OV4 — full bypassPermissions is an opt-in tier, OFF by default (ENABLE_BYPASS_TIER).
BYPASS_TIER_ENABLED: bool = False
# M4 — per-command MCP approver tier (ENABLE_APPROVER_TIER), OFF by default.
APPROVER_TIER_ENABLED: bool = False
# M5 — dual-account cross-review of exec diffs (ENABLE_EXEC_EVALUATOR), OFF by default.
# Advisory only: the evaluator's findings are posted above the diff gate; the human
# ✅/❌ remains the sole merge authority.
EVALUATOR_ENABLED: bool = False

# The env-derived globals load_config() owns — single source of truth so tests can
# snapshot/restore them without a hand-maintained list drifting out of sync.
_CONFIG_GLOBALS = ("CHANNEL_ID", "ALLOWED_USER_IDS", "USE_API_KEY", "BOTS",
                   "PROJECT_DIRS", "BYPASS_TIER_ENABLED", "APPROVER_TIER_ENABLED",
                   "EVALUATOR_ENABLED")


def load_config() -> None:
    """Read every env-derived global (the _CONFIG_GLOBALS) and ensure state dirs
    exist. Called once at startup; tests call it after monkeypatching os.environ.
    Ends by validating (fail-closed)."""
    global CHANNEL_ID, ALLOWED_USER_IDS, USE_API_KEY, BOTS, PROJECT_DIRS
    global BYPASS_TIER_ENABLED, APPROVER_TIER_ENABLED, EVALUATOR_ENABLED
    CHANNEL_ID = int(os.environ["DISCORD_CHANNEL_ID"])
    ALLOWED_USER_IDS = {
        int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").split(",") if x.strip()
    }
    USE_API_KEY = os.environ.get("USE_API_KEY", "").strip().lower() in ("1", "true", "yes", "on")
    BYPASS_TIER_ENABLED = os.environ.get("ENABLE_BYPASS_TIER", "").strip().lower() in ("1", "true", "yes", "on")
    APPROVER_TIER_ENABLED = os.environ.get("ENABLE_APPROVER_TIER", "").strip().lower() in ("1", "true", "yes", "on")
    EVALUATOR_ENABLED = os.environ.get("ENABLE_EXEC_EVALUATOR", "").strip().lower() in ("1", "true", "yes", "on")
    BOTS = {
        n: {"token": os.environ[f"DISCORD_BOT_{n}_TOKEN"],
            "config_dir": BOT_CONFIG_DIRS[n],
            "api_key": os.environ.get(f"ANTHROPIC_API_KEY_{n}")}
        for n in BOT_CONFIG_DIRS
    }
    PROJECT_DIRS = [
        Path(p.strip()).resolve()
        for p in os.environ.get("PROJECT_DIRS", "").split(",") if p.strip()
    ]
    for d in (STATE_DIR, SUMMARIES_DIR, PROJECT_NOTES_DIR):
        d.mkdir(parents=True, exist_ok=True)
    validate_config()


def validate_config() -> None:
    """Fail-closed checks — raise SystemExit rather than run wide open."""
    if not ALLOWED_USER_IDS:
        raise SystemExit(
            "ALLOWED_USER_IDS is empty — refusing to start (fail-closed). "
            "Set at least one Discord user id in .env; an empty list would let "
            "anyone in the channel drive the bots, including bypass-mode execution."
        )
    if USE_API_KEY:
        missing = [n for n, c in BOTS.items() if not c.get("api_key")]
        if missing:
            raise SystemExit(
                f"USE_API_KEY is set but ANTHROPIC_API_KEY_{'/'.join(missing)} is empty. "
                "Provide a per-bot key, or unset USE_API_KEY to use subscription auth."
            )
