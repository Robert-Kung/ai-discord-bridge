## 1. Job registry + streaming + timeouts (milestone 1) — DONE

- [x] 1.1 Job registry: in-memory dict + JSON mirror under `discord-state/jobs/` (load-bearing: restart recovery); exec subprocess spawned with `start_new_session=True`; cap 1 concurrent exec job per project (`bridge/jobs.py`, `running_for_project`)
- [x] 1.2 `!jobs` (id/bot/project/age/status) and `!cancel <id>` (`kill_process_group`: SIGTERM pgid → 5s grace → SIGKILL → `await proc.wait()` before cleanup); the timeout path uses the same killer. Whitelist-gated (upstream command guard).
- [x] 1.3 Exec calls use `--output-format stream-json --verbose` (`build_claude_args(stream=True)`; `--verbose` mandatory — pinned in test_stream); tolerant `parse_stream_event` (ignores blank/unparseable/non-object lines)
- [x] 1.4 Stream `result` event replicates json-path bookkeeping: `session_id` save + last-iteration token accounting (`stream_ctx_tokens`) — verified end-to-end (session saved, ctx=17)
- [x] 1.5 Throttled status updater (≤ 1 edit / `EXEC_STATUS_EDIT_INTERVAL`s), status message posted as a reply, carries job id + `!cancel <id>` hint, trimmed to the edit cap
- [x] 1.6 `EXEC_TIMEOUT` distinct from `CLAUDE_TIMEOUT`; timeout kills the process group and reports it — verified (returncode -15, ~2s not 120s)
- [x] 1.7 Exec jobs hold `cwd_locks[cwd]` (per-project) but NOT `bot_locks` — a long job never stalls the bot's conversation calls; per-project cap of 1
- [x] 1.8 `!jobs`/`!cancel` added to `HELP_TEXT` + `STARTUP_ANNOUNCEMENT`
- [x] 1.9 Tests: registry lifecycle; restart marks `running`→`orphaned`; cancel kills group (real subprocess) + waits before cleanup; stream parser tolerance; `--verbose` pinned; exec timeout distinct; per-project cap; `!cancel` whitelist-gated (tests/test_jobs.py, tests/test_stream.py)
  - NOTE (M1 scope): exec jobs run on the LIVE checkout; the git worktree + diff-review gate is M2. Restart recovery here only marks orphans (re-posting awaiting-review diffs is M2).

## 2. Git worktree + diff-review gate (milestone 2) — DONE

- [x] 2.1 `bridge/worktree.py` helpers: `create_job_worktree` (`git worktree add discord-state/worktrees/<slug>/<id> -b bridge/<id>` from HEAD), `commit_job` (committer identity + task summary + base in message; returns changed?), `job_diff`, `merge_job`, `discard_job`, `remove_worktree`/`delete_branch`, `prune`, `gc_project`
- [x] 2.2 Runner separates **subprocess cwd** (the worktree) from **project identity** (`project` param keys sessions / `cwd_locks` / token accounting). Verified: session keyed by project not worktree; live tree untouched mid-job
- [x] 2.3 On completion: commit to the branch; post `--stat` inline + full diff (inline `diff` code block if ≤1500 chars, else `.txt` attachment truncated at 8 MB); persist `diff.patch` under `discord-state/jobs/<id>/`; ✅/❌ via `pending_actions`
- [x] 2.4 Merge protocol: under `cwd_locks[project]`; clean `git status --porcelain` precondition (dirty → refuse, branch kept); conflict → `git merge --abort` + report + keep branch; never force. Verified against real repos (clean/dirty/conflict-abort)
- [x] 2.5 Reaction timeout → park as `awaiting_review` (never auto-discard); `!merge`/`!discard` act on the surviving branch; startup reloads awaiting-review jobs into the registry + re-lists them (branch + persisted diff survive); the per-project cap counts awaiting-review so a new job can't branch from an un-reviewed HEAD
- [x] 2.6 Startup GC (`bot.main`): `git worktree prune` + remove bridge/<id> branches/worktrees for jobs not awaiting review; non-git dirs fall back to the M1 direct-on-live path (no worktree)
- [x] 2.7 Tests (tests/test_worktree.py, real repos): live tree untouched; commit→diff→merge; dirty-tree refusal; conflict aborts cleanly (no markers); discard cleanup; GC keeps awaiting/removes stale. Plus jobs reload-awaiting test + a fake-claude M2 integration harness (isolation + project identity + merge-to-live)

## 3. Attachment ingestion (milestone 3)

- [ ] 3.1 Download whitelisted-user attachments: sanitized basename only, `attachment.size` cap checked before download, per-message count cap, stored in `discord-state/jobs/<id>/attachments/` (outside the worktree — can never shadow repo files or enter the diff)
- [ ] 3.2 Inject paths as delimited untrusted context
- [ ] 3.3 Tests: whitelist-only; traversal-name neutralized; size/count caps; storage outside worktree

## 4. Verification + Bash relaxation (milestone 4 — gated on egress-exec-isolation PHASE 2)

- [ ] 4.1 Startup gate: assert the phase-2 executor posture (per-container egress canary contract) before enabling anything in this milestone; fail-closed otherwise
- [ ] 4.2 Per-project verify config stored in `discord-state` (NEVER read from the repo/worktree); runner executes it inside the worktree with stripped env (extend `_SUBPROCESS_ENV_DENY` scrubbing to this subprocess) + own timeout
- [ ] 4.3 Append pass/fail + output tail to the result; absent config → explicit "not configured", never a green claim
- [ ] 4.4 Enable Bash for the exec tier only inside the phase-2 executor container (mounts = job worktree + credential); credential/env denies unchanged
- [ ] 4.5 Tests: milestone inert when phase-2 gate unproven; verify config never sourced from worktree; verify env contains no Discord tokens / API keys; denies still fire with Bash on

## 5. Dual-account evaluator (milestone 5 — optional)

- [ ] 5.1 Before the ✅ prompt, optionally hand the diff to the other bot via `converse()` with a skeptical review prompt; post findings above the diff
- [ ] 5.2 Advisory only — human ✅ still gates merge; feature-flagged off by default
- [ ] 5.3 Test: evaluator output is advisory and never auto-merges

## 6. Wrap-up

- [ ] 6.1 Full pytest suite green; `docker compose build` + import smoke; live smoke of a real edit → diff → merge round-trip
- [ ] 6.2 Update SPEC.md / README / SECURITY.md with the job commands, diff-review flow, and the milestone-4 gate rationale
