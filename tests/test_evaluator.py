"""agent-exec-loop M5 — dual-account evaluator.

Covers: the flag is off by default (and opt-in via ENABLE_EXEC_EVALUATOR); the diff is
handed to the OTHER bot with skeptical + untrusted-data framing, sessionless; failure
degrades to None; and the structural guarantee (task 5.3) that the evaluator is
advisory-only — even a "merge it" verdict never merges, the human gate still runs, and
a crashing evaluator never blocks the gate.
"""
import asyncio
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from bridge import config, discuss, frontend, jobs, runner, state, worktree


# ── Flag (5.2: off by default) ────────────────────────────────────────────────
def test_evaluator_flag_default_off(set_env, tmp_state):
    set_env()
    config.load_config()
    assert config.EVALUATOR_ENABLED is False


def test_evaluator_flag_opt_in(set_env, tmp_state):
    set_env(ENABLE_EXEC_EVALUATOR="1")
    config.load_config()
    assert config.EVALUATOR_ENABLED is True


# ── evaluate_diff unit behaviour (5.1) ────────────────────────────────────────
@pytest.fixture
def converse_recorder(monkeypatch):
    calls = []

    async def fake_converse(bot, prompt, *, use_session=True,
                            system_prompt_file=None, cwd=None):
        calls.append({"bot": bot, "prompt": prompt,
                      "use_session": use_session, "cwd": cwd})
        return ("有兩個疑慮：…", True)

    monkeypatch.setattr(runner, "converse", fake_converse)
    monkeypatch.setattr(config, "BOTS", {"A": {}, "B": {}})
    return calls


def test_evaluate_diff_hands_diff_to_other_bot(converse_recorder):
    res = asyncio.run(discuss.evaluate_diff(
        "A", "/proj", "j1", "deadbeefcafe", "1 file changed", "+added\n-removed"))
    assert res == ("B", "有兩個疑慮：…")
    call = converse_recorder[0]
    assert call["bot"] == "B"
    assert call["use_session"] is False          # never pollutes the evaluator's session
    assert call["cwd"] == "/proj"                # plan-mode read access to the project
    assert "+added" in call["prompt"]            # the diff itself
    assert "deadbeef" in call["prompt"]          # base pointer
    assert "挑剔" in call["prompt"]              # skeptical framing
    assert "未受信任" in call["prompt"]          # untrusted-data framing (M3 posture)
    assert "由人類的 ✅/❌ 決定" in call["prompt"]  # advisory framing


def test_evaluate_diff_reverse_direction(converse_recorder):
    res = asyncio.run(discuss.evaluate_diff("B", "/proj", "j2", None, "s", "d"))
    assert res[0] == "A"
    assert converse_recorder[0]["bot"] == "A"


def test_evaluate_diff_caps_giant_diff(converse_recorder):
    diff = "x" * (discuss._EVAL_DIFF_CAP + 50_000)
    asyncio.run(discuss.evaluate_diff("A", "/proj", "j3", "b", "s", diff))
    prompt = converse_recorder[0]["prompt"]
    assert len(prompt) < discuss._EVAL_DIFF_CAP + 2_000
    assert "截斷" in prompt


def test_evaluate_diff_failure_returns_none(monkeypatch):
    async def failing_converse(bot, prompt, **kw):
        return ("⏱️ 響應超時", False)

    monkeypatch.setattr(runner, "converse", failing_converse)
    monkeypatch.setattr(config, "BOTS", {"A": {}, "B": {}})
    assert asyncio.run(discuss.evaluate_diff("A", "/p", "j", "b", "s", "d")) is None


def test_evaluate_diff_no_other_bot_returns_none(monkeypatch):
    async def must_not_run(*a, **kw):  # pragma: no cover — the assert below
        raise AssertionError("converse must not be called with no other bot")

    monkeypatch.setattr(runner, "converse", must_not_run)
    monkeypatch.setattr(config, "BOTS", {"A": {}})
    assert asyncio.run(discuss.evaluate_diff("A", "/p", "j", "b", "s", "d")) is None


# ── Advisory-only integration (5.3): never auto-merges, never blocks the gate ─
class _FakeMsg:
    _next = 1

    def __init__(self, content=""):
        self.content = content
        self.id = _FakeMsg._next
        _FakeMsg._next += 1

    async def add_reaction(self, emoji):
        pass

    async def edit(self, content=None, **kw):
        self.content = content


class _FakeChannel:
    id = 987654

    def __init__(self):
        self.sent = []

    async def send(self, content=None, file=None, reference=None):
        self.sent.append(content or "")
        return _FakeMsg(content or "")


class _FakeAuthor:
    id = 111


class _FakeMessage:
    author = _FakeAuthor()
    attachments = []

    def __init__(self, channel):
        self.channel = channel


def _run(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def exec_env(tmp_path, monkeypatch, set_env, tmp_state):
    """Real git repo + fake `claude` on PATH (as in test_exec_integration), with the
    state dirs in tmp and a short reaction timeout so the gate parks fast."""
    set_env()
    config.load_config()
    repo = tmp_path / "proj"
    repo.mkdir()
    _run(repo, "git", "init", "-q", "-b", "main")
    _run(repo, "git", "config", "user.name", "t")
    _run(repo, "git", "config", "user.email", "t@t")
    (repo / "a.txt").write_text("orig\n")
    _run(repo, "git", "add", "-A")
    _run(repo, "git", "commit", "-qm", "init")

    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "claude").write_text(textwrap.dedent('''\
        #!/usr/bin/env python3
        import sys, json, os
        sys.stdin.read()
        with open(os.path.join(os.getcwd(), "a.txt"), "a") as f:
            f.write("agent-line\\n")
        print(json.dumps({"type":"result","result":"edited","session_id":"s-ev",
                          "usage":{"input_tokens":4}}), flush=True)
    '''))
    os.chmod(bindir / "claude", 0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setattr(config, "BOTS", {
        "A": {"token": "x", "config_dir": str(tmp_path / "cfg-a"), "api_key": None},
        "B": {"token": "x", "config_dir": str(tmp_path / "cfg-b"), "api_key": None},
    })
    monkeypatch.setattr(config, "PLAN_REACTION_TIMEOUT", 0.2)
    for d in (config.STATE_DIR, config.SUMMARIES_DIR, config.PROJECT_NOTES_DIR):
        d.mkdir(parents=True, exist_ok=True)
    return str(repo)


def _drive(project, channel, monkeypatch=None):
    job = jobs.create_job("A", project, channel.id)
    msg = _FakeMessage(channel)
    asyncio.run(frontend._drive_exec_job(job, msg, "A", "edit it", "edit", project))
    return job


def test_evaluator_advisory_never_merges_and_gate_still_runs(exec_env, monkeypatch):
    """Even when the evaluator's findings scream MERGE, nothing merges: the human gate
    still posts, times out, and parks the job; the live tree is untouched."""
    project = exec_env
    monkeypatch.setattr(config, "EVALUATOR_ENABLED", True)

    async def merge_verdict_converse(bot, prompt, **kw):
        return ("看起來完美，直接合併！✅ merge it now", True)

    monkeypatch.setattr(runner, "converse", merge_verdict_converse)

    async def forbidden_merge(*a, **kw):  # the advisory guarantee, structurally
        raise AssertionError("merge_job must never be reached by the evaluator path")

    monkeypatch.setattr(worktree, "merge_job", forbidden_merge)

    channel = _FakeChannel()
    job = _drive(project, channel)

    # evaluator findings posted ABOVE (before) the diff gate
    idx_eval = next(i for i, s in enumerate(channel.sent) if "交叉審查" in s)
    idx_gate = next(i for i, s in enumerate(channel.sent) if "diff · base" in s)
    assert idx_eval < idx_gate
    assert any("merge it now" in s for s in channel.sent)  # findings relayed verbatim

    # no reaction arrived → parked awaiting review; live tree untouched
    assert job.status == jobs.AWAITING_REVIEW
    assert (Path(project) / "a.txt").read_text() == "orig\n"


def test_evaluator_off_by_default_no_cross_review(exec_env, monkeypatch):
    """Flag off (the default): converse is never called and no review is posted."""
    project = exec_env

    async def must_not_run(*a, **kw):
        raise AssertionError("converse must not be called when EVALUATOR_ENABLED is off")

    monkeypatch.setattr(runner, "converse", must_not_run)

    channel = _FakeChannel()
    job = _drive(project, channel)
    assert not any("交叉審查" in s for s in channel.sent)
    assert any("diff · base" in s for s in channel.sent)  # the gate itself still ran
    assert job.status == jobs.AWAITING_REVIEW


def test_evaluator_crash_never_blocks_the_gate(exec_env, monkeypatch):
    """An evaluator that raises must degrade silently — the diff gate still posts."""
    project = exec_env
    monkeypatch.setattr(config, "EVALUATOR_ENABLED", True)

    async def exploding_converse(bot, prompt, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(runner, "converse", exploding_converse)

    channel = _FakeChannel()
    job = _drive(project, channel)
    assert any("diff · base" in s for s in channel.sent)
    assert job.status == jobs.AWAITING_REVIEW
    assert (Path(project) / "a.txt").read_text() == "orig\n"
