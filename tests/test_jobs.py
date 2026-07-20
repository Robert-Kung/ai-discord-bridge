"""Execution-job registry (agent-exec-loop M1): lifecycle, per-project cap, orphan
recovery, and cancel (real process-group kill)."""
import asyncio
import json

import pytest

from bridge import config, jobs


@pytest.fixture(autouse=True)
def _clean_registry():
    jobs.reset_registry_for_tests()
    yield
    jobs.reset_registry_for_tests()


def test_create_persists_and_registers(tmp_state):
    job = jobs.create_job("A", "/home/user/proj", 42)
    assert jobs.get_job(job.id) is job
    assert job.status == jobs.RUNNING
    mirror = config.STATE_DIR / "jobs" / f"{job.id}.json"
    assert mirror.exists()
    data = json.loads(mirror.read_text())
    assert data["bot"] == "A" and data["project"] == "/home/user/proj" and data["status"] == "running"
    # the live subprocess handle is never persisted
    assert "proc" not in data


def test_one_running_job_per_project_cap(tmp_state):
    jobs.create_job("A", "/home/user/proj", 1)
    assert jobs.running_for_project("/home/user/proj") == 1
    assert jobs.running_for_project("/home/user/other") == 0


def test_finished_job_frees_the_project(tmp_state):
    job = jobs.create_job("A", "/home/user/proj", 1)
    jobs.set_status(job, jobs.DONE)
    assert jobs.running_for_project("/home/user/proj") == 0
    assert job.proc is None  # cleared on leaving the running state


def test_recover_marks_running_as_orphaned(tmp_state):
    job = jobs.create_job("B", "/home/user/proj", 7)
    jobs.set_worktree(job, "/wt", "bridge/" + job.id, "abc123")
    jobs.reset_registry_for_tests()  # simulate a restart: in-memory registry gone, mirror stays
    awaiting, orphans = jobs.recover_jobs()
    assert awaiting == []
    data = json.loads((config.STATE_DIR / "jobs" / f"{job.id}.json").read_text())
    assert data["status"] == jobs.ORPHANED
    # orphans are returned (not registered) so the caller can rescue committed branches
    assert [j.id for j in orphans] == [job.id]
    assert orphans[0].branch == "bridge/" + job.id and orphans[0].base == "abc123"
    assert jobs.get_job(job.id) is None


def test_recover_ignores_finished(tmp_state):
    job = jobs.create_job("B", "/home/user/proj", 7)
    jobs.set_status(job, jobs.DONE)
    jobs.reset_registry_for_tests()
    assert jobs.recover_jobs() == ([], [])


def test_rescue_orphan_parks_for_review(tmp_state):
    job = jobs.create_job("B", "/home/user/proj", 7)
    jobs.set_worktree(job, "/wt", "bridge/" + job.id, "abc123")
    jobs.reset_registry_for_tests()
    _, orphans = jobs.recover_jobs()
    jobs.rescue_orphan(orphans[0])
    reloaded = jobs.get_job(job.id)
    assert reloaded is not None and reloaded.status == jobs.AWAITING_REVIEW
    assert jobs.project_occupied("/home/user/proj") is True
    data = json.loads((config.STATE_DIR / "jobs" / f"{job.id}.json").read_text())
    assert data["status"] == jobs.AWAITING_REVIEW  # persisted → survives another restart


def test_recover_reloads_awaiting_review(tmp_state):
    job = jobs.create_job("B", "/home/user/proj", 7)
    jobs.set_worktree(job, "/wt", "bridge/" + job.id, "abc123")
    jobs.set_status(job, jobs.AWAITING_REVIEW)
    jobs.reset_registry_for_tests()  # restart
    awaiting, _ = jobs.recover_jobs()
    assert [j.id for j in awaiting] == [job.id]
    # reloaded into the registry so !merge/!discard work, with worktree metadata intact
    reloaded = jobs.get_job(job.id)
    assert reloaded is not None and reloaded.branch == "bridge/" + job.id and reloaded.base == "abc123"
    assert jobs.project_occupied("/home/user/proj") is True  # blocks a new job until resolved


def test_cancel_without_proc_marks_cancelled(tmp_state):
    job = jobs.create_job("A", "/home/user/proj", 1)
    assert asyncio.run(jobs.cancel_job(job)) is True
    assert job.status == jobs.CANCELLED
    # a second cancel is a no-op (not running)
    assert asyncio.run(jobs.cancel_job(job)) is False


def test_cancel_kills_the_process_group(tmp_state):
    async def _run():
        job = jobs.create_job("A", "/home/user/proj", 1)
        proc = await asyncio.create_subprocess_exec(
            "sleep", "30",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True)
        jobs.attach_proc(job, proc)
        assert proc.returncode is None
        ok = await jobs.cancel_job(job)
        assert ok is True
        # the killed process has been reaped
        assert proc.returncode is not None
        assert job.status == jobs.CANCELLED
    asyncio.run(_run())


def test_render_job_list_shows_only_running(tmp_state):
    a = jobs.create_job("A", "/home/user/proj", 1)
    b = jobs.create_job("B", config.DEFAULT_CWD, 1)
    jobs.set_status(b, jobs.DONE)
    out = jobs.render_job_list()
    assert a.id in out
    assert b.id not in out  # finished jobs are not listed as running
