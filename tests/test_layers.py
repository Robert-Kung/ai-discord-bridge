"""M3 — two trust layers + single chokepoint (spec: agent-trust-layers).

The conversation layer (A↔B debate, summaries, memory) must be structurally incapable
of emitting write/execute permissions; only the human-driven, post-auth execution layer
may. All argv assembly + subprocess launch funnels through one place each. After the
package split these source-scan checks cover the WHOLE `bridge/` package (a second
subprocess launcher added to any module must fail the suite).
"""
import asyncio
import inspect
from pathlib import Path

from bridge import config, memory, runner
from bridge import frontend

REPO = Path(config.__file__).resolve().parent.parent
PKG = REPO / "bridge"
# Every python source that could smuggle in a second chokepoint.
_SOURCES = {p.name: p.read_text() for p in PKG.glob("*.py")}
_SOURCES["bot.py"] = (REPO / "bot.py").read_text()
_ALL_SRC = "\n".join(_SOURCES.values())
RUNNER_SRC = _SOURCES["runner.py"]
FRONTEND_SRC = _SOURCES["frontend.py"]


def _capture_call_claude(monkeypatch):
    """Replace the private chokepoint with a stub that records the mode it was asked
    for, so we can assert what each layer entry emits."""
    seen = {}

    async def stub(bot_name, prompt, *, mode, **kw):
        seen["mode"] = mode
        seen["prompt"] = prompt
        return ("ok", True)

    monkeypatch.setattr(runner, "_call_claude", stub)
    return seen


# ── claude invocation + argv assembly confined to the runner, package-wide ──
def test_claude_invocation_confined_to_runner():
    # The chokepoint invariant is about the CLAUDE subprocess: its argv is assembled once
    # (build_claude_args) and the `claude -p` literal + the assembler live only in the
    # runner. (Other modules may spawn non-claude subprocesses — worktree runs `git` —
    # so a raw create_subprocess_exec count is not the invariant; the claude argv is.)
    assert _ALL_SRC.count("def build_claude_args(") == 1, "claude argv must be assembled in one place"
    assert '"claude", "-p"' in RUNNER_SRC
    for name, src in _SOURCES.items():
        if name == "runner.py":
            continue
        assert '"claude", "-p"' not in src, f"{name} assembles the claude argv"
        assert "build_claude_args" not in src, f"{name} references the claude argv assembler"


def test_only_runner_references_the_private_chokepoint():
    # 1 definition + 2 wrapper calls (converse, execute), ALL inside runner.py.
    assert RUNNER_SRC.count("_call_claude(") == 3
    for name, src in _SOURCES.items():
        if name == "runner.py":
            continue
        assert "_call_claude(" not in src, f"{name} references the private chokepoint"


def test_only_frontend_imports_execute():
    # `execute` is the execution-layer entry; only the frontend may import it.
    for name, src in _SOURCES.items():
        if name in ("runner.py", "frontend.py"):
            continue
        assert "import execute" not in src and "runner.execute" not in src, \
            f"{name} must not reach the execution-layer entry"


# ── conversation layer cannot escalate ──────────────────────────────────────
def test_converse_has_no_mode_parameter():
    params = inspect.signature(runner.converse).parameters
    assert "mode" not in params, "converse() must not expose a mode arg (cannot escalate)"


def test_converse_always_emits_plan(monkeypatch):
    seen = _capture_call_claude(monkeypatch)
    for prompt in ("hello", "!mode bypass", "run acceptEdits please", "x" * 5000):
        asyncio.run(runner.converse("A", prompt))
        assert seen["mode"] == "plan"


def test_execute_refuses_non_execution_mode(monkeypatch):
    _capture_call_claude(monkeypatch)
    import pytest
    for bad in ("plan", "read", "", "PLAN"):
        with pytest.raises(ValueError):
            asyncio.run(runner.execute("A", "do it", mode=bad))


def test_execute_passes_through_execution_modes(monkeypatch):
    seen = _capture_call_claude(monkeypatch)
    for good in ("edit", "acceptEdits", "bypass", "bypassPermissions"):
        asyncio.run(runner.execute("A", "do it", mode=good))
        assert seen["mode"] == good


# ── layer routing: only human-driven edit reaches execute ───────────────────
def test_routing_bot_origin_never_executes():
    assert runner.exec_layer_for(is_bot_msg=True, effective_mode="edit") == "converse"
    assert runner.exec_layer_for(is_bot_msg=True, effective_mode="plan") == "converse"


def test_routing_human_edit_executes_others_converse():
    assert runner.exec_layer_for(is_bot_msg=False, effective_mode="edit") == "execute"
    assert runner.exec_layer_for(is_bot_msg=False, effective_mode="plan") == "converse"
    assert runner.exec_layer_for(is_bot_msg=False, effective_mode="bypass") == "converse"


def test_unwhitelisted_user_gate_precedes_routing():
    # The whitelist `return` for a non-whitelisted human must come BEFORE the
    # routing/execute call sites in the frontend — presence alone is not enough.
    guard = FRONTEND_SRC.index("if message.author.id not in config.ALLOWED_USER_IDS:")
    routing = FRONTEND_SRC.index("exec_layer_for(is_bot_msg, effective_mode)")
    bypass_dispatch = FRONTEND_SRC.index("run_plan_then_execute(message.channel")
    assert guard < routing, "auth guard must precede standard-call routing"
    assert guard < bypass_dispatch, "auth guard must precede bypass dispatch"


# ── conversation output persisted by the harness, not the subprocess ────────
def test_harness_persistence_is_plain_write(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECT_NOTES_DIR", tmp_path / "notes")
    p = memory.save_project_notes("/home/user/proj", "# Notes\nbody")
    assert p.exists() and p.read_text() == "# Notes\nbody"


def test_project_notes_write_rotates_prior_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECT_NOTES_DIR", tmp_path / "notes")
    memory.save_project_notes("/home/user/proj", "old body")
    memory.save_project_notes("/home/user/proj", "new body")
    d = memory.project_notes_dir("/home/user/proj")
    assert (d / "notes.md").read_text() == "new body"
    snaps = [p for p in d.glob("2*.md")]
    assert snaps and any("old body" in s.read_text() for s in snaps)


def test_project_plan_index_is_read_only_to_container():
    # the plan index arrives as a staged copy: the ~/.claude-bot-plan staging dir
    # is presented read-only AT the memory/ path (single-file mounts go inode-stale
    # on atomic replacement — live find 2026-07-13)
    compose = (REPO / "docker-compose.example.yml").read_text()
    m = [ln for ln in compose.splitlines()
         if ".claude-bot-plan:/home/user/.claude-shared/memory" in ln]
    assert m and all(ln.rstrip().endswith(":ro") for ln in m), "staged plan index must be mounted :ro"


# ── inter-agent discussion uses Discord @-mention, never `sibling` ──────────
def test_no_sibling_invocation_anywhere():
    assert "sibling" not in _ALL_SRC, "the bot must not invoke the operator-only sibling CLI"


def test_mention_collaboration_path_exists():
    assert "協作提示" in FRONTEND_SRC and "<@{other_id}>" in FRONTEND_SRC
