"""Background execution-job registry (agent-exec-loop M1).

An in-memory registry plus a JSON mirror under `discord-state/jobs/`. `!jobs` and
`!cancel` operate on the in-memory registry (live jobs only); the mirror exists for
restart recovery (`recover_orphans`) and post-mortem inspection, not for `!jobs` history.
A job tracks one execution-tier task: its id, bot, project (cwd), Discord
channel/status-message, status, and — while running — the subprocess (for cancellation).
The subprocess itself is NOT persisted.

M1 scope: jobs run on the live checkout (the git worktree + diff gate is M2). Restart
recovery here only marks orphaned running jobs; re-posting awaiting-review diffs is M2.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

from bridge import config, runner

log = logging.getLogger("bridge.jobs")

# Terminal + live statuses.
RUNNING = "running"
DONE = "done"
FAILED = "failed"
TIMEOUT = "timeout"
CANCELLED = "cancelled"
ORPHANED = "orphaned"          # process gone after a restart — cannot be resumed
AWAITING_REVIEW = "awaiting_review"  # M2: committed to bridge/<id>, parked for !merge/!discard
MERGING = "merging"            # M2: claimed for a merge — blocks a concurrent merge/discard
_LIVE = {RUNNING}
# Statuses that occupy a project (block a new job) or must survive startup GC.
_OCCUPY = {RUNNING, AWAITING_REVIEW, MERGING}
_KEEP_BRANCH = {RUNNING, AWAITING_REVIEW, MERGING}


@dataclass
class Job:
    id: str
    bot: str
    project: str            # stable project path (identity: sessions/locks/notes)
    channel_id: int
    status: str = RUNNING
    started: float = field(default_factory=time.time)
    msg_id: "int | None" = None
    proc: "object | None" = None   # asyncio subprocess while running; never persisted
    # M2 worktree job (None for M1 / non-git direct jobs)
    worktree: "str | None" = None
    branch: "str | None" = None
    base: "str | None" = None       # base commit the worktree branched from

    def as_dict(self) -> dict:
        return {"id": self.id, "bot": self.bot, "project": self.project,
                "channel_id": self.channel_id, "status": self.status,
                "started": self.started, "msg_id": self.msg_id,
                "worktree": self.worktree, "branch": self.branch, "base": self.base}

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        return cls(id=d["id"], bot=d["bot"], project=d["project"],
                   channel_id=d["channel_id"], status=d.get("status", RUNNING),
                   started=d.get("started", time.time()), msg_id=d.get("msg_id"),
                   worktree=d.get("worktree"), branch=d.get("branch"), base=d.get("base"))


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


def set_worktree(job: Job, worktree: str, branch: str, base: str) -> None:
    job.worktree, job.branch, job.base = worktree, branch, base
    _persist(job)


def attach_proc(job: Job, proc) -> None:
    job.proc = proc


def _job_dir(job_id: str):
    d = _jobs_dir() / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def attachments_dir(job_id: str):
    """Where a job's ingested attachments live — under discord-state/jobs/<id>, OUTSIDE
    the git worktree, so an attachment can never shadow a repo file or enter the diff."""
    d = _job_dir(job_id) / "attachments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sanitize_attachment_name(filename: "str | None") -> str:
    """Reduce a user-supplied attachment filename to a safe basename: strip ALL path
    components (defeats `../` traversal and absolute paths), drop leading dots (no hidden
    files), allow only [A-Za-z0-9._-], cap length. Never returns an empty string."""
    base = Path(filename or "").name       # strips dirs incl. ../ and /abs/paths
    base = base.lstrip(".")                 # no leading-dot hidden/dotfile
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    base = base[:100]
    return base or "attachment"


def save_diff(job: Job, diff: str) -> None:
    """Persist the review diff so a parked job survives a restart."""
    try:
        (_job_dir(job.id) / "diff.patch").write_text(diff)
    except OSError as e:
        log.warning("could not persist diff for job %s: %s", job.id, e)


def load_diff(job_id: str) -> "str | None":
    p = _jobs_dir() / job_id / "diff.patch"
    return p.read_text() if p.exists() else None


def running_for_project(project: str) -> int:
    """Count live (running) jobs for a project."""
    return sum(1 for j in _registry.values() if j.project == project and j.status in _LIVE)


def project_occupied(project: str) -> bool:
    """True if the project has a job RUNNING, AWAITING_REVIEW, or MERGING (an unmerged
    branch). The cap: never start a second job while any holds the project, so a new job
    can't branch from an un-reviewed HEAD (the approved-diff-vs-moved-base TOCTOU)."""
    return any(j.project == project and j.status in _OCCUPY for j in _registry.values())


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


def recover_jobs() -> "tuple[list[Job], list[Job]]":
    """At startup, read the on-disk mirror: mark any job left `running` as orphaned (its
    process died with the previous container), and RELOAD `awaiting_review` jobs into the
    in-memory registry so `!merge`/`!discard` keep working across a restart. Returns
    (awaiting, orphaned). Orphaned jobs are NOT registered — but they are returned so the
    caller can inspect their branch for committed work and rescue it (park as
    awaiting-review) before the startup GC deletes the branch."""
    orphans: list[Job] = []
    awaiting: list[Job] = []
    for p in _jobs_dir().glob("*.json"):
        try:
            data = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        status = data.get("status")
        if status == RUNNING:
            data["status"] = ORPHANED
            try:
                p.write_text(json.dumps(data, ensure_ascii=False))
                orphans.append(Job.from_dict(data))
            except OSError:
                pass
        elif status in (AWAITING_REVIEW, MERGING):
            # a job interrupted mid-merge reloads as awaiting-review: its branch survives
            # and a retry !merge hits the clean-tree precondition (safe if the merge
            # half-applied), so the operator can re-merge or discard.
            data["status"] = AWAITING_REVIEW
            job = Job.from_dict(data)
            _registry[job.id] = job
            awaiting.append(job)
    if orphans:
        log.info("recovered %d orphaned job(s) from a previous run", len(orphans))
    if awaiting:
        log.info("reloaded %d awaiting-review job(s)", len(awaiting))
    return awaiting, orphans


def rescue_orphan(job: Job) -> None:
    """Re-register an orphaned job as awaiting-review: its branch holds committed work
    that must reach the !merge/!discard gate instead of the startup GC."""
    job.status = AWAITING_REVIEW
    _registry[job.id] = job
    _persist(job)


async def rescue_committed_orphans(orphans: "list[Job]") -> int:
    """Startup step: for each orphaned job whose bridge/<id> branch moved past base
    (committed work from a run cut short by the restart), park it awaiting-review and
    persist its diff so the GC keeps the branch and !merge/!discard still work. A
    non-git orphan (no branch/base) is skipped; a branch still at base is left for the
    GC; an unknowable git state is parked fail-safe (a bogus park is one !discard from
    clean). Returns the count rescued. Imported lazily to avoid a jobs↔worktree cycle."""
    from bridge import worktree
    rescued = 0
    for job in orphans:
        if not (job.branch and job.base):
            continue
        try:
            head = await worktree.branch_head(job.project, job.id)
            if head is None or head == job.base:
                continue  # branch at base → nothing committed → let the GC take it
            if await worktree.diff_is_empty(job.project, job.base, job.id) is True:
                continue  # commits that net to zero → nothing to review
            _, full = await worktree.job_diff(job.project, job.base, job.id)
            rescue_orphan(job)
            save_diff(job, full)
            try:
                await worktree.remove_worktree(job.project, job.id)
            except Exception:
                pass
            rescued += 1
            log.info("rescued orphaned job %s (branch bridge/%s holds committed work)",
                     job.id, job.id)
        except Exception:
            log.exception("orphan rescue check failed for job %s — parking to be safe", job.id)
            rescue_orphan(job)
            rescued += 1
    return rescued


def gc_job_state(keep_ids: "set[str]") -> int:
    """Remove on-disk job state (the `<id>.json` mirror and the `<id>/` dir holding
    attachments + diff.patch) for every job NOT in keep_ids. Called at startup with the
    awaiting-review ids so their branch, diff, and attachments survive; everything else
    (done/failed/cancelled/orphaned) is cleaned up — otherwise user-uploaded content
    accumulates forever on the mounted volume."""
    import shutil
    removed = 0
    d = _jobs_dir()
    for p in list(d.iterdir()):
        jid = p.stem if (p.is_file() and p.suffix == ".json") else p.name
        if jid in keep_ids:
            continue
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            removed += 1
        except OSError as e:
            log.warning("could not GC job state %s: %s", p.name, e)
    if removed:
        log.info("GC: removed %d stale job-state entr(ies)", removed)
    return removed


def awaiting_review_ids_by_project() -> dict[str, set[str]]:
    """project → {job ids awaiting review}, for startup GC's keep-set."""
    out: dict[str, set[str]] = {}
    for j in _registry.values():
        if j.status == AWAITING_REVIEW:
            out.setdefault(j.project, set()).add(j.id)
    return out


def _age(started: float) -> str:
    secs = max(0, int(time.time() - started))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    return f"{secs // 3600}h{(secs % 3600) // 60}m"


def render_job_list() -> str:
    from pathlib import Path
    active = [j for j in list_jobs() if j.status in (RUNNING, AWAITING_REVIEW)]
    if not active:
        return "（目前沒有進行中的 job）`@` 觸發新任務。"
    lines = ["**進行中的 jobs**"]
    for j in active:
        proj = "~" if j.project == config.DEFAULT_CWD else Path(j.project).name
        if j.status == AWAITING_REVIEW:
            action = f"`!merge {j.id}` / `!discard {j.id}`"
        else:
            action = f"`!cancel {j.id}`"
        lines.append(f"• `{j.id}` · Bot-{j.bot} · `{proj}` · {_age(j.started)} · {j.status}（{action}）")
    return "\n".join(lines)


def reset_registry_for_tests() -> None:
    """Test hook — clear the in-memory registry between tests."""
    _registry.clear()
