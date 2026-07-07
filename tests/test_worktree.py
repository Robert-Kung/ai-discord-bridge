"""Git worktree lifecycle (agent-exec-loop M2) against real temporary repos.

Exercises the actual git mechanics (no mocking): create → edit-in-worktree → commit →
diff → merge (clean / dirty-refuse / conflict-abort) → discard, plus startup GC.
"""
import asyncio
import subprocess

import pytest

from bridge import config, worktree


def _run(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def project(tmp_path, monkeypatch):
    # a real git repo with one commit, plus a redirected STATE_DIR for worktrees
    repo = tmp_path / "proj"
    repo.mkdir()
    _run(repo, "git", "init", "-q", "-b", "main")
    _run(repo, "git", "config", "user.name", "t")
    _run(repo, "git", "config", "user.email", "t@t")
    (repo / "a.txt").write_text("hello\n")
    _run(repo, "git", "add", "-A")
    _run(repo, "git", "commit", "-qm", "init")
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "state")
    return str(repo)


def test_is_git_repo(project, tmp_path):
    assert worktree.is_git_repo(project)
    assert not worktree.is_git_repo(str(tmp_path / "nope"))


def test_create_edit_commit_diff(project):
    async def go():
        wt, branch, base = await worktree.create_job_worktree(project, "abc123")
        assert branch == "bridge/abc123"
        # live checkout untouched: the worktree is a separate dir
        from pathlib import Path
        assert Path(wt).is_dir() and Path(wt, "a.txt").read_text() == "hello\n"
        Path(wt, "a.txt").write_text("hello\nworld\n")
        Path(wt, "new.txt").write_text("added\n")
        changed = await worktree.commit_job(wt, "abc123", "did stuff", base)
        assert changed is True
        stat, full = await worktree.job_diff(project, base, "abc123")
        assert "a.txt" in stat and "new.txt" in stat
        assert "+world" in full and "+added" in full
        # live checkout is still the original
        assert Path(project, "a.txt").read_text() == "hello\n"
        assert not Path(project, "new.txt").exists()
    asyncio.run(go())


def test_commit_job_no_changes_returns_false(project):
    async def go():
        wt, branch, base = await worktree.create_job_worktree(project, "nochg")
        assert await worktree.commit_job(wt, "nochg", "noop", base) is False
    asyncio.run(go())


def test_merge_clean_applies_to_live(project):
    async def go():
        from pathlib import Path
        wt, branch, base = await worktree.create_job_worktree(project, "m1")
        Path(wt, "a.txt").write_text("hello\nmerged\n")
        await worktree.commit_job(wt, "m1", "edit", base)
        result, _ = await worktree.merge_job(project, "m1", base)
        assert result == "merged"
        assert Path(project, "a.txt").read_text() == "hello\nmerged\n"
    asyncio.run(go())


def test_merge_refuses_dirty_live_tree(project):
    async def go():
        from pathlib import Path
        wt, branch, base = await worktree.create_job_worktree(project, "m2")
        Path(wt, "a.txt").write_text("hello\nfromjob\n")
        await worktree.commit_job(wt, "m2", "edit", base)
        # operator has uncommitted work in the live tree
        Path(project, "a.txt").write_text("hello\nlocal-uncommitted\n")
        result, _ = await worktree.merge_job(project, "m2", base)
        assert result == "dirty"
        # live tree untouched by the refused merge
        assert Path(project, "a.txt").read_text() == "hello\nlocal-uncommitted\n"
    asyncio.run(go())


def test_merge_conflict_aborts_and_keeps_branch(project):
    async def go():
        from pathlib import Path
        wt, branch, base = await worktree.create_job_worktree(project, "m3")
        Path(wt, "a.txt").write_text("hello\njob-line\n")
        await worktree.commit_job(wt, "m3", "edit", base)
        # live branch advances with a conflicting change, committed
        Path(project, "a.txt").write_text("hello\nlive-line\n")
        _run(project, "git", "commit", "-aqm", "live change")
        result, detail = await worktree.merge_job(project, "m3", base)
        assert result == "conflict"
        # merge aborted → no conflict markers, live content preserved
        assert Path(project, "a.txt").read_text() == "hello\nlive-line\n"
        assert "<<<<<<<" not in Path(project, "a.txt").read_text()
        # branch survives for manual merge
        assert "m3" in await worktree.list_bridge_branches(project)
    asyncio.run(go())


def test_merge_refuses_diverged_branch(project):
    # HIGH-finding guard: if the operator switches to a branch that doesn't contain the
    # job's base, merging bridge/<id> would drag in unreviewed history → must refuse.
    async def go():
        from pathlib import Path
        wt, branch, base = await worktree.create_job_worktree(project, "dv")
        Path(wt, "a.txt").write_text("hello\njobedit\n")
        await worktree.commit_job(wt, "dv", "edit", base)
        # create a divergent branch from BEFORE base and advance main past base
        _run(project, "git", "checkout", "-q", "-b", "feature", "HEAD~0")
        _run(project, "git", "checkout", "-q", "--orphan", "sidetrack")
        _run(project, "git", "rm", "-rfq", ".")
        (Path(project) / "z.txt").write_text("side\n")
        _run(project, "git", "add", "-A")
        _run(project, "git", "commit", "-qm", "sidetrack root")
        # HEAD (sidetrack) does not contain base → diverged
        result, detail = await worktree.merge_job(project, "dv", base)
        assert result == "diverged"
        # live tree untouched (still the sidetrack content, no jobedit)
        assert not (Path(project) / "a.txt").exists() or "jobedit" not in (Path(project) / "a.txt").read_text()
        assert "dv" in await worktree.list_bridge_branches(project)
    asyncio.run(go())


def test_discard_removes_worktree_and_branch(project):
    async def go():
        from pathlib import Path
        wt, branch, base = await worktree.create_job_worktree(project, "d1")
        Path(wt, "a.txt").write_text("x\n")
        await worktree.commit_job(wt, "d1", "e", base)
        await worktree.discard_job(project, "d1")
        assert not Path(wt).exists()
        assert "d1" not in await worktree.list_bridge_branches(project)
    asyncio.run(go())


def test_gc_removes_stale_branches_but_keeps_awaiting(project):
    async def go():
        from pathlib import Path
        for jid in ("keep1", "stale1", "stale2"):
            wt, _, base = await worktree.create_job_worktree(project, jid)
            Path(wt, "a.txt").write_text(f"{jid}\n")
            await worktree.commit_job(wt, jid, "e", base)
            await worktree.remove_worktree(project, jid)  # simulate a restart: worktrees gone
        removed = await worktree.gc_project(project, keep_job_ids={"keep1"})
        assert removed == 2
        branches = await worktree.list_bridge_branches(project)
        assert branches == ["keep1"]
    asyncio.run(go())
