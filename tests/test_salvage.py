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
