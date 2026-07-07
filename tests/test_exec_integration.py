"""End-to-end exec-job path against a real git repo + a fake `claude` on PATH.

Drives the REAL code (worktree create → run_streaming_exec in the worktree → commit →
diff → merge) — the coverage the pure-helper tests can't give. Confirms the two safety
properties that matter: the live tree changes ONLY on merge, and the session is keyed by
project identity, not the worktree.
"""
import asyncio
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from bridge import config, runner, sessions, state, worktree


def _run(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo_and_fake_claude(tmp_path, monkeypatch):
    repo = tmp_path / "proj"
    repo.mkdir()
    _run(repo, "git", "init", "-q", "-b", "main")
    _run(repo, "git", "config", "user.name", "t")
    _run(repo, "git", "config", "user.email", "t@t")
    (repo / "a.txt").write_text("orig\n")
    _run(repo, "git", "add", "-A")
    _run(repo, "git", "commit", "-qm", "init")

    # fake claude: append to a.txt + add newfile.txt in its cwd, emit stream-json
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "claude").write_text(textwrap.dedent('''\
        #!/usr/bin/env python3
        import sys, json, os
        sys.stdin.read()
        with open(os.path.join(os.getcwd(), "a.txt"), "a") as f:
            f.write("agent-line\\n")
        with open(os.path.join(os.getcwd(), "newfile.txt"), "w") as f:
            f.write("new\\n")
        print(json.dumps({"type":"assistant","message":{"content":[
            {"type":"tool_use","name":"Edit","input":{"file_path":"a.txt"}}]}}), flush=True)
        print(json.dumps({"type":"result","result":"edited","session_id":"s-int",
                          "usage":{"input_tokens":4}}), flush=True)
    '''))
    os.chmod(bindir / "claude", 0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(config, "BOTS", {"A": {"token": "x", "config_dir": str(tmp_path / "cfg"), "api_key": None}})
    return str(repo)


def test_full_exec_path_live_tree_changes_only_on_merge(repo_and_fake_claude):
    project = repo_and_fake_claude

    async def go():
        wt, branch, base = await worktree.create_job_worktree(project, "int1")
        before = (Path(project) / "a.txt").read_text()
        reply, outcome = await runner.run_streaming_exec(
            "A", "edit it", mode="edit", cwd=wt, project=project,
            on_trace=lambda l: None, on_proc=lambda p: None,
            should_abort=lambda: False, timeout=30)
        assert outcome == runner.EXEC_DONE and reply == "edited"
        # ISOLATION: live tree untouched by the run
        assert (Path(project) / "a.txt").read_text() == before
        assert not (Path(project) / "newfile.txt").exists()
        # PROJECT IDENTITY: session keyed by project, not the worktree path
        assert sessions.load_session("A", project) == "s-int"
        assert sessions.load_session("A", wt) is None
        assert state.session_ctx_tokens[("A", project)] == 4

        changed = await worktree.commit_job(wt, "int1", "edit it", base)
        assert changed
        stat, full = await worktree.job_diff(project, base, "int1")
        assert "newfile.txt" in stat and "+agent-line" in full

        # DISCARD leaves the live tree untouched
        # (branch/worktree removed; nothing merged)
        stat_before_discard = (Path(project) / "a.txt").read_text()
        # merge applies it
        result, _ = await worktree.merge_job(project, "int1", base)
        assert result == "merged"
        assert (Path(project) / "a.txt").read_text() == "orig\nagent-line\n"
        assert (Path(project) / "newfile.txt").read_text() == "new\n"

    asyncio.run(go())


def test_discard_never_touches_live_tree(repo_and_fake_claude):
    project = repo_and_fake_claude

    async def go():
        wt, branch, base = await worktree.create_job_worktree(project, "int2")
        await runner.run_streaming_exec(
            "A", "edit it", mode="edit", cwd=wt, project=project,
            on_trace=lambda l: None, on_proc=lambda p: None,
            should_abort=lambda: False, timeout=30)
        await worktree.commit_job(wt, "int2", "edit it", base)
        await worktree.discard_job(project, "int2")
        # live tree is exactly the original — a discarded job leaves no trace
        assert (Path(project) / "a.txt").read_text() == "orig\n"
        assert not (Path(project) / "newfile.txt").exists()
        assert "int2" not in await worktree.list_bridge_branches(project)

    asyncio.run(go())
