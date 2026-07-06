"""Background execution-job registry (agent-exec-loop M1).

An in-memory registry plus a JSON mirror under `discord-state/jobs/` so `!jobs` history
and restart recovery survive a container restart. A job tracks one execution-tier task:
its id, bot, project (cwd), Discord channel/status-message, status, and — while running —
the subprocess (for cancellation). The subprocess itself is NOT persisted.

M1 scope: jobs run on the live checkout (the git worktree + diff gate is M2). Restart
recovery here only marks orphaned running jobs; re-posting awaiting-review diffs is M2.
"""
from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import dataclass, field

from bridge import config, runner

log = logging.getLogger("bridge.jobs")

# Terminal + live statuses.
RUNNING = "running"
DONE = "done"
FAILED = "failed"
TIMEOUT = "timeout"
CANCELLED = "cancelled"
ORPHANED = "orphaned"   # process gone after a restart — cannot be resumed in M1
_LIVE = {RUNNING}


@dataclass
class Job:
    id: str
    bot: str
    project: str            # the resolved cwd
    channel_id: int
    status: str = RUNNING
    started: float = field(default_factory=time.time)
    msg_id: "int | None" = None
    proc: "object | None" = None   # asyncio subprocess while running; never persisted

    def as_dict(self) -> dict:
        return {"id": self.id, "bot": self.bot, "project": self.project,
                "channel_id": self.channel_id, "status": self.status,
                "started": self.started, "msg_id": self.msg_id}


# The live registry (id → Job).
_registry: dict[str, Job] = {}


def _jobs_dir():
    d = config.STATE_DIR / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _persist(job: Job) -> None:
    try:
        p = _jobs_dir() / f"{job.id}.json"
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(job.as_dict(), ensure_ascii=False))
        tmp.replace(p)
    except OSError as e:
        log.warning("could not persist job %s: %s", job.id, e)


def create_job(bot: str, project: str, channel_id: int) -> Job:
    jid = secrets.token_hex(3)
    while jid in _registry or (_jobs_dir() / f"{jid}.json").exists():
        jid = secrets.token_hex(3)
    job = Job(id=jid, bot=bot, project=project, channel_id=channel_id)
    _registry[jid] = job
    _persist(job)
    return job


def get_job(job_id: str) -> "Job | None":
    return _registry.get(job_id)


def set_status(job: Job, status: str) -> None:
    job.status = status
    if status not in _LIVE:
        job.proc = None
    _persist(job)


def set_msg(job: Job, msg_id: int) -> None:
    job.msg_id = msg_id
    _persist(job)


def attach_proc(job: Job, proc) -> None:
    job.proc = proc


def running_for_project(project: str) -> int:
    """Count live (running) jobs for a project — used to cap concurrency at 1/project."""
    return sum(1 for j in _registry.values() if j.project == project and j.status in _LIVE)


def list_jobs() -> list[Job]:
    return sorted(_registry.values(), key=lambda j: j.started, reverse=True)


async def cancel_job(job: Job) -> bool:
    """Kill the job's process group (TERM→KILL→reap) and mark it cancelled. Returns
    False if the job was not running."""
    if job.status not in _LIVE:
        return False
    proc = job.proc
    set_status(job, CANCELLED)  # set first so the driver sees it and suppresses "done"
    if proc is not None:
        await runner.kill_process_group(proc)
    return True


def recover_orphans() -> int:
    """At startup, load the on-disk mirror and mark any job left `running` as orphaned
    (its process died with the previous container). Returns the number recovered."""
    n = 0
    d = _jobs_dir()
    for p in d.glob("*.json"):
        try:
            data = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("status") == RUNNING:
            data["status"] = ORPHANED
            try:
                p.write_text(json.dumps(data, ensure_ascii=False))
                n += 1
            except OSError:
                pass
    if n:
        log.info("recovered %d orphaned job(s) from a previous run", n)
    return n


def _age(started: float) -> str:
    secs = max(0, int(time.time() - started))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    return f"{secs // 3600}h{(secs % 3600) // 60}m"


def render_job_list() -> str:
    jobs = [j for j in list_jobs() if j.status in _LIVE]
    if not jobs:
        return "（目前沒有執行中的 job）用 `!cancel <id>` 取消、`@` 觸發新任務。"
    lines = ["**執行中的 jobs**"]
    for j in jobs:
        from pathlib import Path
        proj = "~" if j.project == config.DEFAULT_CWD else Path(j.project).name
        lines.append(f"• `{j.id}` · Bot-{j.bot} · `{proj}` · {_age(j.started)} · {j.status}"
                     f"（`!cancel {j.id}`）")
    return "\n".join(lines)


def reset_registry_for_tests() -> None:
    """Test hook — clear the in-memory registry between tests."""
    _registry.clear()
