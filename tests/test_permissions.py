"""M1 — execution permissions: settings.json deny family, sandbox-off, argv order,
the OV1 settings canary, and default-closed bypass (spec: execution-permissions).

Behaviour was empirically validated against claude 2.1.179 (see preflight-findings.md:
permissions.deny enforces in headless; --allowedTools does not restrict; bwrap can't
start in the container). These tests lock the resulting in-repo config + arg assembly
so CI catches drift without needing a live claude.
"""
import asyncio
import json
from pathlib import Path

import bot

REPO = Path(bot.__file__).resolve().parent
SETTINGS = json.loads((REPO / "settings.json").read_text())
DENY = SETTINGS["permissions"]["deny"]


# ── 2.1 / 2.6 / 2.10 — deny family present (single source of credential denial) ──
def test_settings_denies_credential_reads():
    assert any("credentials.json" in d for d in DENY)
    # the dedicated bot dirs (where creds are mounted) are deny-read
    assert any(".claude-bot-a" in d for d in DENY)
    assert any(".claude-bot-b" in d for d in DENY)


def test_settings_denies_env_dump():
    assert "Bash(env)" in DENY
    assert any("printenv" in d for d in DENY)


def test_settings_denies_network_fetch():
    assert any(d.startswith("Bash(curl") for d in DENY)
    assert any(d.startswith("Bash(wget") for d in DENY)
    assert "WebFetch" in DENY


def test_settings_is_strict_json_no_unknown_top_keys():
    # claude SILENTLY IGNORES a settings file that fails validation, so an unknown
    # top-level key would evaporate the whole deny family. Keep it minimal/known.
    assert set(SETTINGS) <= {"permissions", "sandbox"}


# ── 2.7 — sandbox explicitly disabled (no "on but silently absent") ─────────
def test_sandbox_explicitly_disabled():
    assert SETTINGS["sandbox"]["enabled"] is False


# ── 2.9 (Issue 4) — argv order; no variadic flag; prompt never in argv ──────
def test_build_args_order_and_settings_first():
    args = bot.build_claude_args("acceptEdits", session_id="sid123",
                                 system_prompt_file="/tmp/sp.md")
    assert args[:4] == ["claude", "-p", "--output-format", "json"]
    i_settings, i_mode = args.index("--settings"), args.index("--permission-mode")
    assert i_settings < i_mode  # value flags before anything that could vary
    assert args[i_settings + 1] == bot.BRIDGE_SETTINGS_PATH
    assert args[i_mode + 1] == "acceptEdits"
    assert args[args.index("--resume") + 1] == "sid123"
    assert args[args.index("--append-system-prompt-file") + 1] == "/tmp/sp.md"


def test_build_args_no_variadic_or_allowlist_flags():
    args = bot.build_claude_args("plan", session_id="s", system_prompt_file="/tmp/x")
    # gate 0.1: no allow-list (doesn't restrict); old disallowedTools removed (deny→settings)
    assert "--allowedTools" not in args
    assert "--disallowedTools" not in args


def test_build_args_omits_optional_flags_when_absent():
    args = bot.build_claude_args("plan")
    assert "--resume" not in args
    assert "--append-system-prompt-file" not in args
    # --settings is NEVER optional — it carries the deny family on every call
    assert "--settings" in args


def test_prompt_is_never_in_argv():
    # the prompt is fed via stdin; build_claude_args takes no prompt at all
    args = bot.build_claude_args("acceptEdits", session_id="s")
    assert not any("from " in a or len(a) > 200 for a in args)


# ── 2.8 (OV1) — canary decision fails closed when deny did not fire ─────────
def test_canary_fails_closed_on_no_denial():
    # corrupted/unloaded settings → claude runs the denied command → empty denials
    assert bot.canary_passed({}) is False
    assert bot.canary_passed({"permission_denials": []}) is False


def test_canary_passes_only_on_real_bash_denial():
    assert bot.canary_passed({"permission_denials": [{"tool_name": "Bash"}]}) is True
    # a non-Bash denial alone is not the canary firing (the canary attempts Bash)
    assert bot.canary_passed({"permission_denials": [{"tool_name": "Read"}]}) is False


# ── canary classification — "deny dropped" (refuse) vs "cannot run" (retry) ─────
# The OAuth-expiry incident (188 container restarts): a not-logged-in canary was
# treated like a security failure and crash-looped the container. The classifier
# must keep those two failures apart so only a genuine settings drop fails hard.
def _ok_body(denied=True):
    return json.dumps({"is_error": False,
                       "permission_denials": [{"tool_name": "Bash"}] if denied else []}).encode()


def test_classify_ok_when_ran_and_denied():
    assert bot.classify_canary(0, _ok_body(denied=True)) == bot.CANARY_OK


def test_classify_deny_dropped_when_ran_but_not_denied():
    # claude ran cleanly (rc=0, is_error False) but no Bash denial → settings dropped
    assert bot.classify_canary(0, _ok_body(denied=False)) == bot.CANARY_DENY_DROPPED


def test_classify_cannot_run_on_nonzero_rc():
    # the "Not logged in" body claude emits comes with rc=1 → retryable, NOT deny-dropped
    body = json.dumps({"is_error": True, "result": "Not logged in · Please run /login",
                       "permission_denials": []}).encode()
    assert bot.classify_canary(1, body) == bot.CANARY_CANNOT_RUN


def test_classify_cannot_run_on_is_error_even_with_rc0():
    # defense: an error body that somehow exits 0 still never reached the perm layer
    body = json.dumps({"is_error": True, "permission_denials": []}).encode()
    assert bot.classify_canary(0, body) == bot.CANARY_CANNOT_RUN


def test_classify_cannot_run_on_timeout_or_unparseable():
    assert bot.classify_canary(None, b"") == bot.CANARY_CANNOT_RUN      # timeout (rc None)
    assert bot.classify_canary(0, b"not json") == bot.CANARY_CANNOT_RUN  # garbage body


# ── 3.2 / 3.4 — full bypass is opt-in, default closed ───────────────────────
def test_bypass_default_closed(monkeypatch):
    monkeypatch.setattr(bot, "ALLOWED_USER_IDS", {111})
    monkeypatch.setattr(bot, "BYPASS_TIER_ENABLED", False)
    assert bot.bypass_allowed(111) is False  # whitelisted but tier off → no


def test_bypass_requires_tier_and_whitelist(monkeypatch):
    monkeypatch.setattr(bot, "ALLOWED_USER_IDS", {111})
    monkeypatch.setattr(bot, "BYPASS_TIER_ENABLED", True)
    assert bot.bypass_allowed(111) is True
    assert bot.bypass_allowed(999) is False  # tier on but not whitelisted → no


class _FakeChannel:
    id = 9999


def test_cmd_mode_refuses_bypass_when_tier_off(monkeypatch, tmp_state):
    monkeypatch.setattr(bot, "ALLOWED_USER_IDS", {111})
    monkeypatch.setattr(bot, "BYPASS_TIER_ENABLED", False)
    msg = asyncio.run(bot.cmd_mode(_FakeChannel(), "bypass", 111))
    assert "未啟用" in msg
    # and the channel mode was NOT switched to bypass
    assert bot.load_channel_state(_FakeChannel.id).get("mode", "plan") != "bypass"


def test_cmd_mode_allows_edit_tier(monkeypatch, tmp_state):
    monkeypatch.setattr(bot, "ALLOWED_USER_IDS", {111})
    monkeypatch.setattr(bot, "BYPASS_TIER_ENABLED", False)
    bot.STATE_DIR.mkdir(parents=True, exist_ok=True)  # tmp_state redirects but doesn't mkdir
    msg = asyncio.run(bot.cmd_mode(_FakeChannel(), "edit", 111))
    assert "edit" in msg
    assert bot.load_channel_state(_FakeChannel.id)["mode"] == "edit"
