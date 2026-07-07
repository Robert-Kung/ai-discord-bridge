"""Git worktree lifecycle for execution-tier jobs (agent-exec-loop M2).

Every exec-tier task runs in a throwaway git worktree branched from the project's HEAD,
never on the live checkout. On completion the changes are committed to the `bridge/<id>`
branch — the BRANCH, not the worktree, is the recoverable source of truth (it lives in the
mounted repo and survives a container restart even after the worktree is GC'd). Merge into
the live branch happens only on human ✅, under a strict protocol (clean live tree required,
`merge --abort` on conflict, never force).

Worktrees live under `discord-state/worktrees/<project-slug>/<job-id>/` (a mounted volume),
NOT container /tmp — /tmp is overlay fs and a restart would vaporize the working copies
while leaving stale `.git/worktrees` admin metadata in the mounted repo.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from bridge import config, sessions

log = logging.getLogger("bridge.worktree")

_COMMITTER = ("-c", "user.name=ai-discord-bridge", "-c", "user.email=bridge@localhost")


class WorktreeError(RuntimeError):
    pass


async def _git(cwd: "str | Path", *args: str, timeout: float = 120) -> tuple[int, str, str]:
    """Run one git command in `cwd`; return (rc, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(cwd), *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return (124, "", "git timed out")
    return (proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"))


def is_git_repo(project: str) -> bool:
    return (Path(project) / ".git").is_dir()


def worktrees_root() -> Path:
    return config.STATE_DIR / "worktrees"


def worktree_path(project: str, job_id: str) -> Path:
    return worktrees_root() / sessions._cwd_slug(project) / job_id


def branch_name(job_id: str) -> str:
    return f"bridge/{job_id}"


async def create_job_worktree(project: str, job_id: str) -> tuple[str, str, str]:
    """`git worktree add <wt> -b bridge/<id> HEAD` from the project's HEAD. Returns
    (worktree_path, branch, base_commit). Raises WorktreeError on failure."""
    rc, base, err = await _git(project, "rev-parse", "HEAD")
    if rc != 0:
        raise WorktreeError(f"rev-parse HEAD failed: {err.strip()}")
    base = base.strip()
    wt = worktree_path(project, job_id)
    wt.parent.mkdir(parents=True, exist_ok=True)
    branch = branch_name(job_id)
    rc, _, err = await _git(project, "worktree", "add", str(wt), "-b", branch, "HEAD")
    if rc != 0:
        raise WorktreeError(f"worktree add failed: {err.strip()}")
    return str(wt), branch, base


async def commit_job(worktree: str, job_id: str, summary: str, base: str) -> bool:
    """Stage everything in the worktree and commit to its branch. Returns True if a commit
    was made, False if the agent left no changes (nothing to review)."""
    await _git(worktree, "add", "-A")
    rc, _, _ = await _git(worktree, "diff", "--cached", "--quiet")
    if rc == 0:
        return False  # no staged changes
    msg = f"bridge job {job_id}\n\n{summary.strip()[:500]}\n\nBase: {base}"
    rc, _, err = await _git(worktree, *_COMMITTER, "commit", "-m", msg)
    if rc != 0:
        raise WorktreeError(f"commit failed: {err.strip()}")
    return True


async def job_diff(project: str, base: str, job_id: str) -> tuple[str, str]:
    """Return (diffstat, full_diff) for base..bridge/<id> (read from the shared object db)."""
    branch = branch_name(job_id)
    _, stat, _ = await _git(project, "diff", "--stat", f"{base}..{branch}")
    _, full, _ = await _git(project, "diff", f"{base}..{branch}")
    return stat, full


async def merge_job(project: str, job_id: str) -> tuple[str, str]:
    """Merge bridge/<id> into the project's current branch. Returns (result, detail) where
    result is 'merged' | 'dirty' | 'conflict' | 'error'. NEVER forces. On conflict it aborts
    (leaving no conflict markers in the live tree) and keeps the branch for manual merge."""
    rc, porcelain, err = await _git(project, "status", "--porcelain")
    if rc != 0:
        return ("error", err.strip())
    if porcelain.strip():
        return ("dirty", "live checkout has uncommitted changes")
    branch = branch_name(job_id)
    rc, out, err = await _git(project, "merge", "--no-ff", "-m", f"Merge bridge job {job_id}", branch)
    if rc == 0:
        return ("merged", out.strip())
    await _git(project, "merge", "--abort")
    return ("conflict", (err or out).strip())


async def remove_worktree(project: str, job_id: str) -> None:
    """Remove the worktree working copy (the branch survives)."""
    wt = worktree_path(project, job_id)
    await _git(project, "worktree", "remove", "--force", str(wt))


async def delete_branch(project: str, job_id: str) -> None:
    await _git(project, "branch", "-D", branch_name(job_id))


async def discard_job(project: str, job_id: str) -> None:
    """Remove the worktree AND delete the branch — a rejected/cancelled job leaves no trace."""
    await remove_worktree(project, job_id)
    await delete_branch(project, job_id)


async def prune(project: str) -> None:
    await _git(project, "worktree", "prune")


async def list_bridge_branches(project: str) -> list[str]:
    """Job ids that still have a bridge/<id> branch in this project."""
    rc, out, _ = await _git(project, "for-each-ref", "--format=%(refname:short)", "refs/heads/bridge")
    if rc != 0:
        return []
    return [ln.split("/", 1)[1] for ln in out.splitlines() if ln.startswith("bridge/")]


async def gc_project(project: str, keep_job_ids: "set[str]") -> int:
    """Startup GC for one project: prune stale worktree metadata, then remove any
    bridge/<id> branch (and its worktree) whose job id is not in keep_job_ids (i.e. not an
    awaiting-review job). Returns the number of branches removed."""
    if not is_git_repo(project):
        return 0
    await prune(project)
    removed = 0
    for jid in await list_bridge_branches(project):
        if jid in keep_job_ids:
            continue
        await discard_job(project, jid)
        removed += 1
    if removed:
        log.info("GC: removed %d stale bridge branch(es) in %s", removed, project)
    return removed
