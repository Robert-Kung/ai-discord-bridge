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

## 3. Attachment ingestion (milestone 3) — DONE

- [x] 3.1 Whitelisted-user attachments downloaded via `_save_attachments`: `sanitize_attachment_name` strips path components (`../`, absolute, leading dot) → safe basename; `attachment.size` checked against `EXEC_ATTACH_MAX_BYTES` before read; `EXEC_ATTACH_MAX_COUNT` slice; stored in `discord-state/jobs/<id>/attachments/` (outside the worktree — can't shadow repo files or enter the diff); collisions deduped
- [x] 3.2 Paths injected into the exec prompt via `_attachment_context` — delimited, explicitly framed as untrusted DATA not instructions
- [x] 3.3 Tests (tests/test_attachments.py): traversal/absolute/hidden-name neutralized; size + count caps; count-slice-then-size ordering; dedupe; dir outside any worktree; whitelist-only; untrusted framing

## 4. Verification + Bash relaxation (milestone 4 — gated on egress-exec-isolation PHASE 2) — DONE (code; live smoke = operator)

- [x] 4.1 Gate `runner.m4_live()` = `EXEC_BASH_ENABLED` (ENABLE_EXEC_BASH, off by default) AND `EXECUTOR_SOCKET` set — i.e. the phase-2 executor whose Discord-deny egress canary executor.py proved before serving. Single-container/frontend → unproven → inert regardless of the flag (fail-closed by construction)
- [x] 4.2 Per-project verify command read ONLY from `STATE_DIR/verify/<slug>` (never the repo/worktree); `runner.run_verify` runs it in the worktree with a stripped env (`build_verify_env` reuses `_SUBPROCESS_ENV_DENY` — no Discord token / API key), own `VERIFY_TIMEOUT`, own process group killed on timeout. Runs executor-side over the IPC verify op (`_validate_verify_request` pins project→whitelist, workdir→worktree root)
- [x] 4.3 `frontend._post_verify` posts pass/fail + output tail above the diff gate; absent config → explicit "未設定 verify" (never a green claim); any failure degrades to a note, never blocks the gate
- [x] 4.4 Exec-tier Bash: `write_exec_settings` (executor startup when m4_live) derives an exec settings file from settings.json = base deny family + `Bash` allow; `_exec_request_to_spawn` uses it ONLY for live stream jobs. Deny outranks allow so credential/env/curl/wget stay denied (defense-in-depth); base deny-only settings for every other call
- [x] 4.5 Tests (tests/test_verify_bash.py, 16): tier inert when flag off / single-container; exec settings keep the whole deny family with Bash allowed and are used only for live stream; verify config never sourced from worktree (planted .bridge-verify ignored); verify runs in the worktree, times out, and its env + a real subprocess cannot see a Discord token / API key; IPC round-trip live vs inert; workdir-outside-worktree refused

## 5. Dual-account evaluator (milestone 5 — optional) — DONE

- [x] 5.1 `discuss.evaluate_diff` hands the diff (capped at `_EVAL_DIFF_CAP`, truncation flagged) to the OTHER bot via `converse()` — sessionless, plan-mode cwd = live checkout for Read/Grep context, skeptical + untrusted-data framing; `frontend._post_evaluator_review` posts findings above the diff gate
- [x] 5.2 Advisory by construction: the evaluator path never touches `pending_actions` or the merge path; any failure (call or post) degrades to a log line and the human gate always follows. Flag `ENABLE_EXEC_EVALUATOR` (config.EVALUATOR_ENABLED, `_CONFIG_GLOBALS`), OFF by default
- [x] 5.3 Tests (tests/test_evaluator.py): flag default-off/opt-in; other-bot selection both directions; diff + framing in prompt; giant-diff cap; failure/no-other-bot → None; integration (real repo + fake claude): a "merge it" verdict never merges (forbidden `merge_job` trap), findings post before the gate, gate parks on timeout, live tree untouched; flag-off → no converse call; crashing evaluator never blocks the gate

## 6. Wrap-up

- [ ] 6.1 Full pytest suite green; `docker compose build` + import smoke; live smoke of a real edit → diff → merge round-trip
- [ ] 6.2 Update SPEC.md / README / SECURITY.md with the job commands, diff-review flow, and the milestone-4 gate rationale
