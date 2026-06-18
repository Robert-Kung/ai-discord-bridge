"""M3 — two trust layers + single chokepoint (spec: agent-trust-layers).

The conversation layer (A↔B debate, summaries, memory) must be structurally
incapable of emitting write/execute permissions; only the human-driven, post-auth
execution layer may. All argv assembly + subprocess launch funnels through one
place each.
"""
import asyncio
import inspect
from pathlib import Path

import bot

REPO = Path(bot.__file__).resolve().parent
SRC = (REPO / "bot.py").read_text()


def _capture_call_claude(monkeypatch):
    """Replace the private chokepoint with a stub that records the mode it was
    asked for, so we can assert what each layer entry emits."""
    seen = {}

    async def stub(bot_name, prompt, *, mode, **kw):
        seen["mode"] = mode
        seen["prompt"] = prompt
        return ("ok", True)

    monkeypatch.setattr(bot, "_call_claude", stub)
    return seen


# ── 4.1 / 4.4 — exactly one assembler + one launcher ────────────────────────
def test_single_subprocess_launcher_and_arg_assembler():
    assert SRC.count("create_subprocess_exec(") == 1, "subprocess must launch in one place"
    assert SRC.count("def build_claude_args(") == 1, "argv must be assembled in one place"
    # the `claude -p` argv literal lives only in build_claude_args
    assert SRC.count('"claude", "-p"') == 1


def test_layers_do_not_call_chokepoint_directly():
    # conversation/execution entries are the only callers of _call_claude; no other
    # code path constructs an invocation. (run_settings_canary uses the shared
    # launcher with build_claude_args, not _call_claude.)
    direct = SRC.count("_call_claude(")
    # 1 definition + 2 wrapper calls (converse, execute)
    assert direct == 3, f"unexpected _call_claude callers: {direct}"


# ── 4.2 / 4.5 — conversation layer cannot escalate ──────────────────────────
def test_converse_has_no_mode_parameter():
    params = inspect.signature(bot.converse).parameters
    assert "mode" not in params, "converse() must not expose a mode arg (cannot escalate)"


def test_converse_always_emits_plan(monkeypatch):
    seen = _capture_call_claude(monkeypatch)
    for prompt in ("hello", "!mode bypass", "run acceptEdits please", "x" * 5000):
        asyncio.run(bot.converse("A", prompt))
        assert seen["mode"] == "plan"


def test_execute_refuses_non_execution_mode(monkeypatch):
    _capture_call_claude(monkeypatch)
    import pytest
    for bad in ("plan", "read", "", "PLAN"):
        with pytest.raises(ValueError):
            asyncio.run(bot.execute("A", "do it", mode=bad))


def test_execute_passes_through_execution_modes(monkeypatch):
    seen = _capture_call_claude(monkeypatch)
    for good in ("edit", "acceptEdits", "bypass", "bypassPermissions"):
        asyncio.run(bot.execute("A", "do it", mode=good))
        assert seen["mode"] == good


# ── 4.5 / 4.6 — layer routing: only human-driven edit reaches execute ───────
def test_routing_bot_origin_never_executes():
    # any bot-origin mention is the conversation layer, even in an edit channel
    assert bot.exec_layer_for(is_bot_msg=True, effective_mode="edit") == "converse"
    assert bot.exec_layer_for(is_bot_msg=True, effective_mode="plan") == "converse"


def test_routing_human_edit_executes_others_converse():
    assert bot.exec_layer_for(is_bot_msg=False, effective_mode="edit") == "execute"
    assert bot.exec_layer_for(is_bot_msg=False, effective_mode="plan") == "converse"
    # an unknown/leftover mode never silently becomes execution
    assert bot.exec_layer_for(is_bot_msg=False, effective_mode="bypass") == "converse"


def test_unwhitelisted_user_gate_precedes_routing():
    # The whitelist `return` for a non-whitelisted human must come BEFORE the
    # routing/execute call site — presence alone is not enough (a guard placed after
    # execute() would still "exist" but protect nothing). Assert relative position.
    guard = SRC.index("if message.author.id not in ALLOWED_USER_IDS:")
    # both execution dispatch points in on_message — the standard-call routing and the
    # bypass dispatch — must come AFTER the whitelist return (each appears once).
    routing = SRC.index("exec_layer_for(is_bot_msg, effective_mode)")
    bypass_dispatch = SRC.index("run_plan_then_execute(message.channel")
    assert guard < routing, "auth guard must precede standard-call routing"
    assert guard < bypass_dispatch, "auth guard must precede bypass dispatch"


# ── 4.7 — conversation output persisted by the harness, not the subprocess ──
def test_harness_persistence_is_plain_write(tmp_path, monkeypatch):
    # The live harness writers are save_summary / save_project_notes — plain Python
    # writes, independent of any subprocess permission mode (the conversation layer
    # runs plan and cannot write files via the agent). Exercise the project-notes path.
    monkeypatch.setattr(bot, "PROJECT_NOTES_DIR", tmp_path / "notes")
    p = bot.save_project_notes("/home/user/proj", "# Notes\nbody")
    assert p.exists() and p.read_text() == "# Notes\nbody"


def test_project_notes_write_rotates_prior_snapshot(tmp_path, monkeypatch):
    # No free-form clobber: a second write snapshots the prior notes.md, so accumulated
    # state can never be silently overwritten by a flush.
    monkeypatch.setattr(bot, "PROJECT_NOTES_DIR", tmp_path / "notes")
    bot.save_project_notes("/home/user/proj", "old body")
    bot.save_project_notes("/home/user/proj", "new body")
    d = bot.project_notes_dir("/home/user/proj")
    assert (d / "notes.md").read_text() == "new body"
    snaps = [p for p in d.glob("2*.md")]
    assert snaps and any("old body" in s.read_text() for s in snaps)


def test_project_plan_index_is_read_only_to_container():
    # The clobber guarantee for the operator's index is the :ro mount, not a helper:
    # the execution path can read project_plan.md as context but cannot overwrite it.
    import re
    compose = (REPO / "docker-compose.example.yml").read_text()
    m = [ln for ln in compose.splitlines() if "memory/project_plan.md" in ln]
    assert m and all(ln.rstrip().endswith(":ro") for ln in m), "project_plan.md must be mounted :ro"


# ── 4.8 — inter-agent discussion uses Discord @-mention, never `sibling` ────
def test_no_sibling_invocation_in_bot():
    assert "sibling" not in SRC, "the bot must not invoke the operator-only sibling CLI"


def test_mention_collaboration_path_exists():
    # the two bots reach each other via an @-mention hint, not a CLI
    assert "協作提示" in SRC and "<@{other_id}>" in SRC
