# Unattended auto mode + outbound media

## Why

Every exec job today ends at a human gate: the diff posts and waits for ✅/`!merge`. That is the right default, but it caps the bridge at "one task per human glance" — the operator cannot say "take this task list and run" and come back to merged, verified work. The pieces that make unattended operation safe already exist separately: the M4 verify tier (per-project test command, `:ro` config, unforgeable), the M5 evaluator tier (cross-account skeptical review, currently advisory free-text), and the job-loss-family fix (committed work can no longer be silently deleted). What is missing is a mode that wires them into the merge decision — and a way for the bot to show its work (mockups, screenshots-as-files, generated images) without the operator shelling into the host.

## What Changes

- **`!mode auto` (opt-in tier, `ENABLE_AUTO_MERGE`, default off)**: exec jobs run as today (worktree + branch + diff), but gate resolution becomes machine-driven:
  1. **verify must be configured AND pass** for the project (`discord-verify/<slug>`); unconfigured or failed → park as awaiting-review (never auto-merge unverified work).
  2. **evaluator verdict becomes structured**: the cross-review prompt requires a first-line verdict `VERDICT: approve|reject|unsure` followed by findings. `approve` → auto-merge under the existing merge protocol (clean tree, ancestor check, no force, abort on conflict — unchanged). `reject`/`unsure`/evaluator unavailable → park with findings posted.
  3. Every auto-merge posts a full audit message (diffstat, verify tail, evaluator verdict) — same visibility as today, minus the wait.
- **Auto-continue (bounded)**: in auto mode an operator message may carry a task list; after each auto-merged job the bridge feeds the next task as a new exec job (fresh worktree from the new HEAD). Hard bounds: `AUTO_MAX_JOBS` per invocation (default 5) and the existing per-project occupancy rule; any park stops the chain and reports.
- **Outbound media (`exec-task-loop` addition)**: the bot can attach files from the job's workspace to its Discord replies — explicit request marker from the agent (a `DISCORD_ATTACH: <path>` line in the reply), whitelist of extensions (png/jpg/gif/svg/html/txt/pdf), per-file and per-message size caps, paths resolved strictly inside the job worktree or job dir (no traversal, no symlink escape). Enables posting mockups and generated images for review.

## Non-goals（明確不做）

- No autonomous scheduling / cron; auto mode still starts from an operator message.
- No relaxation of the merge protocol (force merges, dirty-tree merges) and no bypass of the verify `:ro` posture.
- `!cancel` semantics unchanged (unconditional discard). Human gate remains the default mode.
- No multi-channel routing changes; no new egress.

## Acceptance criteria

1. Auto mode off (default): behaviour byte-identical to today; flag absent → `!mode auto` refuses (fail-closed, same pattern as bypass/approve tiers).
2. Auto mode on: a job with passing verify + `VERDICT: approve` merges without human action and posts the audit message; failing/unconfigured verify or non-approve verdict parks for `!merge`/`!discard` with reasons.
3. Evaluator output that does not parse as a verdict → treated as `unsure` (park), never as approve.
4. Task-list chain stops at first park/failure and at `AUTO_MAX_JOBS`; each chained job branches from the post-merge HEAD.
5. Outbound attachment: only whitelisted extensions under caps leave the host; a path outside the worktree/job dir is refused and logged; nothing is attached unless the agent explicitly marked it.
6. Tests cover: verdict parsing (incl. adversarial "VERDICT: approve" buried mid-text — only first line counts), park-on-unverified, chain bounds, attachment path traversal refusal.

## Impact

- `bridge/frontend.py`: gate resolution branch (auto path), chain driver, attachment sender; `bridge/discuss.py`: structured verdict prompt + parser; `bridge/config.py`: `ENABLE_AUTO_MERGE`, `AUTO_MAX_JOBS`; `settings/compose`: env plumbing (both services, explicit-environment pattern).
- Specs: `exec-task-loop` modified (gate modes, outbound media); `agent-trust-layers` modified (auto tier posture note).
- Depends on: fix/job-loss-family (PR #20) merged first — auto mode chains must not run on a base where committed work can vanish.
- Security review gate required（涉及權限/外部輸出）：auto-merge 是把「人審」換成「verify+evaluator 雙訊號」，SECURITY.md 需新增一節姿態說明；outbound attachment 是新的資料外流面（受白名單/caps/路徑限制）。
