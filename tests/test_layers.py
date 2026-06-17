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
    # on_message returns for a non-whitelisted human BEFORE routing, so the only way
    # to reach execute() is whitelist-passed + edit. Assert the guard exists in source.
    assert "if message.author.id not in ALLOWED_USER_IDS:" in SRC


# ── 4.7 — conversation output persisted by the harness, not the subprocess ──
def test_harness_persistence_is_plain_write(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "PLANS_DIR", tmp_path / "plans")
    monkeypatch.setattr(bot, "PROJECT_PLAN_INDEX", tmp_path / "memory" / "project_plan.md")
    # full plan → plans/ (mode-independent harness write)
    p = bot.save_plan("my-feature", "# Plan\nbody")
    assert p.exists() and p.read_text() == "# Plan\nbody"


def test_plan_index_append_rotate_snapshots_prior(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "PLANS_DIR", tmp_path / "plans")
    idx = tmp_path / "memory" / "project_plan.md"
    idx.parent.mkdir(parents=True)
    idx.write_text("# Index\n- old entry\n")
    monkeypatch.setattr(bot, "PROJECT_PLAN_INDEX", idx)
    bot.append_plan_index("- new entry")
    body = idx.read_text()
    assert "old entry" in body and "new entry" in body  # append, not overwrite
    # prior version snapshotted (recoverable) — never a free-form clobber
    snaps = list((tmp_path / "plans").glob("_project_plan.*.bak.md"))
    assert snaps and "old entry" in snaps[0].read_text()


# ── 4.8 — inter-agent discussion uses Discord @-mention, never `sibling` ────
def test_no_sibling_invocation_in_bot():
    assert "sibling" not in SRC, "the bot must not invoke the operator-only sibling CLI"


def test_mention_collaboration_path_exists():
    # the two bots reach each other via an @-mention hint, not a CLI
    assert "協作提示" in SRC and "<@{other_id}>" in SRC
