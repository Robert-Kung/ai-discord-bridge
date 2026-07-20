"""Salvage paths for the job-loss family (fix/job-loss-family, 2026-07-20).

Three ways finished work used to be silently deleted, each now parked for review:
1. done + agent self-committed (the 2026-07-18 live hit) — covered in test_worktree
2. timeout/failed with work in the worktree/branch → _salvage_partial_work
3. restart orphan whose branch moved past base → bot.py startup rescue (jobs.rescue_orphan)
"""
import asyncio
import subprocess

import pytest

from bridge import config, jobs, worktree
from bridge.frontend import _salvage_partial_work


def _run(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


class _Chan:
    def __init__(self):
        self.sent = []

    async def send(self, msg):
        self.sent.append(msg)


@pytest.fixture
def project(tmp_path, monkeypatch):
    repo = tmp_path / "proj"
    repo.mkdir()
    _run(repo, "git", "init", "-q", "-b", "main")
    _run(repo, "git", "config", "user.name", "t")
    _run(repo, "git", "config", "user.email", "t@t")
    (repo / "a.txt").write_text("hello\n")
    _run(repo, "git", "add", "-A")
    _run(repo, "git", "commit", "-qm", "init")
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "state")
    (tmp_path / "state").mkdir()
    jobs.reset_registry_for_tests()
    return str(repo)


def test_salvage_parks_self_committed_work_on_timeout(project):
    async def go():
        from pathlib import Path
        job = jobs.create_job("A", project, 1)
        wt, branch, base = await worktree.create_job_worktree(project, job.id)
        jobs.set_worktree(job, wt, branch, base)
        Path(wt, "half.txt").write_text("task 1 of 4\n")
        _run(wt, "git", "add", "-A")
        _run(wt, "git", "-c", "user.name=agent", "-c", "user.email=a@a",
             "commit", "-qm", "task 1")
        jobs.set_status(job, jobs.TIMEOUT)  # driver sets this before salvaging
        chan = _Chan()
        assert await _salvage_partial_work(job, chan, project, wt, "apply prompt") is True
        assert job.status == jobs.AWAITING_REVIEW
        assert await worktree.branch_head(project, job.id) != base  # branch survives
        assert not Path(wt).exists()                                # worktree removed
        assert jobs.load_diff(job.id) and "half.txt" in jobs.load_diff(job.id)
        assert any("保留待審" in m for m in chan.sent)
        # parked work still merges cleanly through the normal gate
        result, _ = await worktree.merge_job(project, job.id, base)
        assert result == "merged"
        assert Path(project, "half.txt").read_text() == "task 1 of 4\n"
    asyncio.run(go())


def test_salvage_returns_false_when_no_work(project):
    async def go():
        job = jobs.create_job("A", project, 1)
        wt, branch, base = await worktree.create_job_worktree(project, job.id)
        jobs.set_worktree(job, wt, branch, base)
        jobs.set_status(job, jobs.TIMEOUT)
        chan = _Chan()
        assert await _salvage_partial_work(job, chan, project, wt, "p") is False
        assert job.status == jobs.TIMEOUT  # unchanged — caller proceeds to discard
        assert chan.sent == []
    asyncio.run(go())


def test_salvage_stages_uncommitted_leftovers_too(project):
    async def go():
        from pathlib import Path
        job = jobs.create_job("A", project, 1)
        wt, branch, base = await worktree.create_job_worktree(project, job.id)
        jobs.set_worktree(job, wt, branch, base)
        Path(wt, "wip.txt").write_text("uncommitted at kill time\n")
        jobs.set_status(job, jobs.FAILED)
        chan = _Chan()
        assert await _salvage_partial_work(job, chan, project, wt, "p") is True
        assert jobs.load_diff(job.id) and "wip.txt" in jobs.load_diff(job.id)
    asyncio.run(go())


def test_salvage_parks_when_diff_check_errors_but_branch_has_commits(project, monkeypatch):
    # H1 regression: a git error during the diff step must NOT be read as net-zero and
    # delete a branch that holds the agent's committed work.
    async def go():
        from pathlib import Path
        job = jobs.create_job("A", project, 1)
        wt, branch, base = await worktree.create_job_worktree(project, job.id)
        jobs.set_worktree(job, wt, branch, base)
        Path(wt, "committed.txt").write_text("real work\n")
        _run(wt, "git", "add", "-A")
        _run(wt, "git", "-c", "user.name=a", "-c", "user.email=a@a", "commit", "-qm", "t1")
        jobs.set_status(job, jobs.TIMEOUT)

        async def _boom(*a, **k):
            return None  # git couldn't answer

        monkeypatch.setattr(worktree, "diff_is_empty", _boom)
        chan = _Chan()
        assert await _salvage_partial_work(job, chan, project, wt, "p") is True
        assert job.status == jobs.AWAITING_REVIEW
        assert await worktree.branch_head(project, job.id) != base  # branch preserved
    asyncio.run(go())


def test_salvage_parks_when_commit_raises_after_self_commit(project, monkeypatch):
    # M1 regression: commit_job raising (e.g. a failing pre-commit hook on leftovers)
    # AFTER the agent already self-committed must still park, not discard.
    async def go():
        from pathlib import Path
        job = jobs.create_job("A", project, 1)
        wt, branch, base = await worktree.create_job_worktree(project, job.id)
        jobs.set_worktree(job, wt, branch, base)
        Path(wt, "committed.txt").write_text("real work\n")
        _run(wt, "git", "add", "-A")
        _run(wt, "git", "-c", "user.name=a", "-c", "user.email=a@a", "commit", "-qm", "t1")
        jobs.set_status(job, jobs.TIMEOUT)

        async def _raise(*a, **k):
            raise worktree.WorktreeError("commit failed: hook rejected")

        monkeypatch.setattr(worktree, "commit_job", _raise)
        chan = _Chan()
        assert await _salvage_partial_work(job, chan, project, wt, "p") is True
        assert job.status == jobs.AWAITING_REVIEW
        assert await worktree.branch_head(project, job.id) != base
    asyncio.run(go())


def test_salvage_true_net_zero_returns_false(project):
    # commit that cancels out (add then remove) → proven net-zero → nothing to salvage
    async def go():
        from pathlib import Path
        job = jobs.create_job("A", project, 1)
        wt, branch, base = await worktree.create_job_worktree(project, job.id)
        jobs.set_worktree(job, wt, branch, base)
        f = Path(wt, "a.txt")
        orig = f.read_text()
        f.write_text("changed\n")
        _run(wt, "git", "add", "-A")
        _run(wt, "git", "-c", "user.name=a", "-c", "user.email=a@a", "commit", "-qm", "c1")
        f.write_text(orig)  # revert
        _run(wt, "git", "add", "-A")
        _run(wt, "git", "-c", "user.name=a", "-c", "user.email=a@a", "commit", "-qm", "c2")
        jobs.set_status(job, jobs.TIMEOUT)
        chan = _Chan()
        assert await _salvage_partial_work(job, chan, project, wt, "p") is False
    asyncio.run(go())


def test_rescue_committed_orphans_survives_gc(project):
    # M2: end-to-end startup rescue — a running-orphan with a committed branch must be
    # parked (registered awaiting-review) AND survive the subsequent gc_project.
    async def go():
        from pathlib import Path
        job = jobs.create_job("B", project, 1)
        wt, branch, base = await worktree.create_job_worktree(project, job.id)
        jobs.set_worktree(job, wt, branch, base)
        Path(wt, "work.txt").write_text("committed by agent\n")
        _run(wt, "git", "add", "-A")
        _run(wt, "git", "-c", "user.name=a", "-c", "user.email=a@a", "commit", "-qm", "t")
        jobs.reset_registry_for_tests()          # simulate restart: registry gone, mirror stays
        _, orphans = jobs.recover_jobs()          # running → orphaned, returned not registered
        assert [j.id for j in orphans] == [job.id]

        n = await jobs.rescue_committed_orphans(orphans)
        assert n == 1
        reloaded = jobs.get_job(job.id)
        assert reloaded is not None and reloaded.status == jobs.AWAITING_REVIEW

        keep = jobs.awaiting_review_ids_by_project()
        await worktree.gc_project(project, keep.get(project, set()))
        assert await worktree.branch_head(project, job.id) is not None  # branch survived GC
    asyncio.run(go())


def test_rescue_skips_non_git_and_base_only_orphans(project):
    async def go():
        # non-git orphan (no branch/base) is skipped
        j1 = jobs.create_job("A", project, 1)  # no set_worktree → branch/base None
        # base-only orphan (branch exists but at base) is left for the GC
        j2 = jobs.create_job("A", project, 1)
        wt, branch, base = await worktree.create_job_worktree(project, j2.id)
        jobs.set_worktree(j2, wt, branch, base)  # no commits on the branch
        jobs.reset_registry_for_tests()
        _, orphans = jobs.recover_jobs()
        n = await jobs.rescue_committed_orphans(orphans)
        assert n == 0
        assert jobs.get_job(j1.id) is None and jobs.get_job(j2.id) is None
    asyncio.run(go())
