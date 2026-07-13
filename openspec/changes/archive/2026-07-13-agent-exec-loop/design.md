## Context

The execution path is one synchronous `claude -p` per `@`-mention: `_call_claude` → `_run_claude_subprocess`, `--output-format json` (all-or-nothing output), `CLAUDE_TIMEOUT=300` with `proc.kill()` (orphans grandchildren), serialized under `bot_locks`/`cwd_locks` with no cancellation. `acceptEdits` writes directly to the live mounted checkout. There is no branch/diff/merge gate. This change makes execution a first-class, observable, reversible job. Review D3 reframed two milestones as security changes: verification executes agent-influenced code, and Bash relaxation replaces per-command approval with outcome review — both live behind the egress **phase 2** gate.

## Goals / Non-Goals

**Goals:**
- Long tasks run as cancellable background jobs that never stall conversation calls, with live progress.
- Execution-tier edits land on a throwaway git worktree, reviewed as a diff, merged only on ✅ under a safe protocol; completed work survives restarts and reaction timeouts.
- A real post-task test result (never a fake green), gated with Bash as one trust decision.
- Attachments (screenshots/logs) become usable context, safely.

**Non-Goals:**
- Changing conversation-layer behavior or the trust-layer guarantees (unchanged).
- MCP tool servers, multi-channel routing, autonomous scheduling.
- Replacing the `approve` tier (kept for non-git one-shot commands).
- Bash in the single-container posture (rejected at review D3: diff gate cannot see the rw-mount writable surface; see proposal).

## Decisions

- **Milestone order: jobs/streaming first, git gate second.** M1 is zero-security-surface, delivers immediate value, and builds the job registry/id scheme M2's worktree lifecycle keys off. (Resequenced at review; original order had the git gate first.)
- **Worktree per exec job, not branch-in-place.** `git worktree add <path> -b bridge/<job-id>` from HEAD of the target project.
  - **Location:** `discord-state/worktrees/<project-slug>/<job-id>/` — a mounted volume, NOT container `/tmp` (overlay fs: a restart vaporizes working copies while stale `.git/worktrees/` metadata persists in the mounted repo).
  - **The branch is the source of truth.** At job end the bridge commits all changes to `bridge/<job-id>` (committer identity `bridge/<job-id>`, message includes the task prompt summary + base commit). Diff, review, and merge are then recoverable from the branch ref alone even if the worktree is GC'd.
  - **Startup GC:** `git worktree prune` in every mounted project; worktrees/branches for jobs not in the registry are removed only if unreviewed-and-unstarted — `awaiting-review` branches are kept.
  - *Uncommitted-work caveat:* worktree starts from committed HEAD; operator's local uncommitted changes are absent. Documented; the result message states the base commit.
- **Merge protocol (✅) — the one step that touches the live checkout, so it is strict:**
  1. Runs under `cwd_locks[project]` (the *project* lock, not the worktree's).
  2. Precondition: `git status --porcelain` clean in the live checkout; dirty → refuse with the branch name and manual instructions (do NOT attempt a merge that could entangle operator WIP).
  3. Merge `bridge/<job-id>` into the checked-out branch; on conflict → `git merge --abort`, report, keep the branch. Never leave conflict markers; never force.
  4. Every failure message carries exact recovery commands (`git merge bridge/<id>` / `git branch -D bridge/<id>`).
- **Reaction timeout parks, never deletes.** A completed job whose review window lapses becomes `awaiting-review`; `!merge <id>` / `!discard <id>` act on the surviving branch. Timed GC (days) for parked branches. Rationale: deleting a 30-minute job because the operator was 5 minutes away is data loss by design.
- **Diff delivered as `--stat` inline + full diff as `.txt` attachment** (mobile previews `.txt`; `.patch` doesn't). Diffs < ~1500 chars go inline as a code block; attachments cap at the upload limit with a truncated fallback + stat. Reactions ✅/❌ reuse `pending_actions`. The persisted `diff.patch` is also written under `discord-state/jobs/<id>/` so a restart can re-post it.
- **Job registry: in-memory dict + on-disk mirror that is load-bearing, not history.** `jobs[job_id] = {bot, project, worktree, proc, status, started, msg_id}`; JSON mirror under `discord-state/jobs/`. On restart: `running` → marked `orphaned` (process is gone); `awaiting-review` → re-posted from the persisted diff. **Cap: 1 concurrent exec job per project** (matches `cwd_locks` philosophy; also removes the approved-diff-vs-moved-base TOCTOU).
- **Kill semantics:** spawn with `start_new_session=True`; cancel/timeout sends SIGTERM to the **pgid**, escalates to SIGKILL after a grace period, then `await proc.wait()` **before** worktree removal (removing under a dying process races). The existing timeout path must route through this same killer. `!cancel` authorization: whitelist-gated (as all commands are); stated and tested, not owner-scoped at personal scale.
- **Streaming via `--output-format stream-json --verbose`.** The `--verbose` flag is mandatory (CLI errors without it in `-p` mode; verified live on claude 2.1.199) — pinned in a `build_claude_args` test. Tolerant JSONL parser (ignore unknown event types). A throttled updater (≤ ~1 edit / 2 s, **per channel** — N jobs in one channel share Discord's edit bucket) edits the status message with a compact recent-actions trace, trimmed to the 2000-char edit cap. The final `result` event **must replicate the json-path bookkeeping**: `session_id` save and last-iteration token accounting (bot.py:733–749), or exec jobs silently starve the token-flush machinery.
- **Project identity ≠ subprocess cwd.** Today `cwd` keys four things at once: subprocess cwd, session file, `cwd_locks`, and summary/notes injection. Exec jobs pass `cwd=worktree` for the subprocess but keep **project identity** (the real project path) for sessions, notes/summaries, locks, and the registry — otherwise every job burns a fresh session, litters `discord-state`, and the agent silently loses exactly the project memory the bridge exists to provide. The runner signature separates the two (coordinated with `bridge-restructure`).
- **Timeouts split.** `EXEC_TIMEOUT` (default e.g. 1800 s) for exec jobs, `CLAUDE_TIMEOUT` unchanged for conversation. Timeout kills the group and reports worktree state (diff-so-far preserved).
- **Verification is per-project, opt-in, and configured OUTSIDE the repo.** The verify command lives in `discord-state` per-project config — **never** in a file inside the repo/worktree (`.bridge-verify` was rejected at review: the agent would author its own verify command into its diff, and the verify subprocess would inherit the bridge's own env, which contains the Discord tokens — the env-strip protects only `claude` children). Even with the command source fixed, the agent still controls what the command *executes* (conftest, package.json scripts), so verification is irreducibly agent-grade execution: stripped env (reuse `_SUBPROCESS_ENV_DENY` scrubbing), own timeout, and **the same gate as Bash relaxation (egress phase 2)**. Absent config → "no verify configured" (never a fake green).
- **Bash relaxation runs only in the phase-2 executor container** (Anthropic-only egress, mounts = job worktree + credential). That single posture makes the diff gate sound: the writable surface IS the reviewed surface. Startup check asserts the phase-2 egress canary contract before enabling; fail-closed otherwise. Credential/env denies unchanged (defense-in-depth).
- **Attachment ingestion hardening:** whitelisted-user attachments only; `attachment.filename` reduced to a sanitized basename; `attachment.size` checked against a cap before download (count cap per message too); stored in `discord-state/jobs/<id>/attachments/` — **outside the worktree**, so an attachment can never shadow repo files or enter the diff. Paths injected into the prompt as delimited untrusted content, consistent with `_is_trusted` framing.
- **Evaluator milestone reuses the A/B pair.** Before the human ✅, optionally hand the diff to the other bot via `converse()` with a skeptical review prompt; post findings above the diff. Advisory — human ✅ still gates merge. Zero new infra; feature-flagged off by default.

## Risks / Trade-offs

- [Worktree proliferation / disk] → 1 job per project + startup prune/GC + parked-branch GC after days.
- [stream-json parser drift on Claude Code upgrades] → tolerate unknown event types; `--verbose` prerequisite pinned by test; canary theme already covers version drift.
- [Process-group kill misses a double-forked child] → documented residual; TERM→KILL + pgid covers the normal tree.
- [Merge protocol refusals annoy the operator] → deliberate: refusal messages carry exact manual commands; safety of the live tree wins.
- [Attachment as injection vector] → content-not-instructions framing, whitelist-only, sanitized names, size caps, outside-worktree storage.

## Migration Plan

- Milestones, independently shippable in order: (1) jobs + streaming + timeouts, (2) git worktree + diff gate + merge protocol + restart recovery, (3) attachments, (4) verification + Bash (gated on egress phase 2 executor), (5) evaluator.
- Backward compatible: with no verify config and milestone 4 off, existing behavior becomes "edits go through a diff gate" — a strict safety improvement.
- Rollback: revert re-enables direct `acceptEdits` on the live tree; no data migration (job dirs under `discord-state` are inert).
