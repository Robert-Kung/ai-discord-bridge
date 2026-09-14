# Unattended auto mode + outbound media

## Why

Every exec job today ends at a human gate: the diff posts and waits for ✅/`!merge`. That is the right default, but it caps the bridge at "one task per human glance" — the operator cannot say "take this task list and run" and come back to merged, verified work. The pieces that make unattended operation safe already exist separately: the M4 verify tier (per-project test command, `:ro` config, unforgeable), the M5 evaluator tier (cross-account skeptical review, currently advisory free-text), and the job-loss-family fix (committed work can no longer be silently deleted). What is missing is a mode that wires them into the merge decision — and a way for the bot to show its work (mockups, screenshots-as-files, generated images) without the operator shelling into the host.

## What Changes

- **`!mode auto` (opt-in tier, `ENABLE_AUTO_MERGE` + whitelist, default off)**: exec jobs run as today (worktree + branch + diff), but gate resolution becomes machine-driven:
  1. **Tier gate mirrors bypass/approve, not the env flag alone**: `!mode auto` is honoured only when `ENABLE_AUTO_MERGE` is set AND the author is in `ALLOWED_USER_IDS`. Auto carries *more* authority than bypass, so it requires *both* the same way bypass/approve do. `VALID_MODES` gains `"auto"` and the dispatch-time re-validation list gains `"auto"` (else auto is not re-checked per message).
  2. **Preconditions must be live or auto refuses to serve**: enabling auto requires the verify path (`m4_live`) AND the evaluator (`EVALUATOR_ENABLED`) to be available; if either is missing, `!mode auto` is refused with the reason (fail-closed, not a silent park-everything no-op).
  3. **verify must be configured AND pass** for the project (`discord-verify/<slug>`); unconfigured, failed, or verify-unavailable → park as awaiting-review (never auto-merge unverified work). The auto path runs its own `request_verify` with exception→park; it does NOT reuse `_post_verify` (which swallows exceptions).
  4. **evaluator verdict becomes structured**: the cross-review prompt requires a first-line verdict `VERDICT: approve|reject|unsure` followed by findings. Parsing tolerates surrounding markdown/preamble on that first line but reads only the evaluator's own first line. `approve` → auto-merge under the existing merge protocol (clean tree, ancestor check, no force, abort on conflict — unchanged). `reject`/`unsure`/unparseable/evaluator unavailable → park with findings posted.
  5. **Dependency changes are surfaced, not gated**: enabling auto mode is itself an acceptance of autonomous package use, and the supply-chain code already executes at verify time regardless of who merges (see design §2). So auto mode does NOT park on dependency changes. Instead, the per-job audit message prominently lists any dep/lockfile/config changes (`worktree.dependency_changes`), so the operator's post-hoc read of the audit trail sees exactly which merges touched dependencies.
  6. Every auto-merge posts a full audit message (diffstat, verify tail, evaluator verdict, dep-note) — same visibility as today, minus the wait.
- **Auto-continue (bounded)**: in auto mode an operator message may carry a task list; after each auto-merged job the bridge feeds the next task as a new exec job (fresh worktree from the new HEAD). The chain advances only when the prior job actually reached `DONE` (merge succeeded) — an `approve` verdict that still parks (dirty tree / diverged base / conflict) stops the chain. Hard bounds: `AUTO_MAX_JOBS` per invocation (default 5, validated int > 0) and the existing per-project occupancy rule; any park stops the chain and reports.
- **Outbound media (`exec-task-loop` addition)**: the bot can attach files from its workspace to its Discord replies — explicit request marker from the agent (a `DISCORD_ATTACH: <path>` line in the reply), whitelist of extensions (png/jpg/jpeg/gif/svg/html/txt/pdf), per-file (≤8 MB) and per-message (≤4 files) size caps. Scope: both exec-job replies and chat/converse replies. Containment root depends on the reply's workspace — an exec job resolves under its job worktree or job dir; a chat reply resolves under the live project checkout — enforced with `Path.resolve()` + `is_relative_to` (NOT a string prefix, which a `worktree-evil` sibling would defeat). Confidentiality is bounded by the operator-only channel (`ALLOWED_USER_IDS`): attached bytes reach only the whitelisted operator, the same audience as reply text today. Enables posting mockups and generated images for review.

## Non-goals（明確不做）

- No autonomous scheduling / cron; auto mode still starts from an operator message.
- No relaxation of the merge protocol (force merges, dirty-tree merges) and no bypass of the verify `:ro` posture.
- `!cancel` semantics unchanged (unconditional discard). Human gate remains the default mode.
- No multi-channel routing changes and no new egress *host* — outbound media is a new *data class* (binary files) over the existing Discord egress to the operator-only channel, not a new destination.

## Acceptance criteria

1. Auto mode off (default): behaviour byte-identical to today; flag absent OR author not whitelisted → `!mode auto` refuses (fail-closed, same pattern as bypass/approve tiers). `VALID_MODES`/dispatch re-validation include `"auto"`.
2. Enabling auto with the verify path or the evaluator unavailable → `!mode auto` refuses to serve with the reason (never fails open, never silently parks everything).
3. Auto mode on: a job with passing verify + first-line `VERDICT: approve` merges without human action and posts the audit message; failing/unconfigured/unavailable verify or non-approve/unparseable verdict parks for `!merge`/`!discard` with reasons.
4. Evaluator output whose own first line does not parse as a verdict → treated as `unsure` (park), never as approve. First-line parsing tolerates markdown/preamble (e.g. `**VERDICT: approve**`) so a well-formed approval is not false-parked, but a verdict buried past the first line never counts.
5. Dependency changes do NOT block an auto-merge, but every audit message lists changed dep/lockfile/config paths when present.
6. Task-list chain advances only on `status == DONE`; it stops at the first park/failure and at `AUTO_MAX_JOBS`, and each chained job branches from the post-merge HEAD.
7. Outbound attachment: only whitelisted extensions under caps leave the host; a path outside the reply's containment root (worktree/job dir for exec, live checkout for chat) is refused via `is_relative_to` after symlink resolution and logged; nothing is attached unless the agent explicitly marked it.
8. The verdict channel is NOT claimed to be exhaustively sealed: SECURITY posture documents that a diff-content prompt injection may steer the evaluator into genuinely emitting `approve`, and that verify + evaluator are correlated (both see the same diff), so "two green signals" means "not obviously bad", not "safe".
9. Tests cover: verdict parsing (incl. adversarial buried verdict AND realistic markdown/preamble AND an LLM-obedience red-team, not just the parser), refuse-to-serve on missing preconditions, park-on-unverified/park-on-reject/park-on-evaluator-unavailable, chain bounds + gate-on-DONE, attachment path traversal/symlink refusal with a whitelisted-extension target, and cap refusals.

## Impact

- `bridge/frontend.py`: gate resolution branch (auto path, own `request_verify`), chain driver (gate on DONE), attachment sender (exec + chat, `is_relative_to`), dep-note in audit; `bridge/discuss.py`: structured verdict prompt + tolerant parser; `bridge/config.py`: `ENABLE_AUTO_MERGE`, `AUTO_MAX_JOBS`, `VALID_MODES += "auto"`, `approve → "manual"` (was `"default"`); `bridge/runner.py`: `build_claude_args("manual", …)` (was `"default"`); `bridge/trust.py`: auto tier requires flag + whitelist; `settings/compose`: env plumbing (both services, explicit-environment pattern).
- Housekeeping folded in (touches the same mode plumbing): migrate the `approve` tier off the now-undocumented `--permission-mode default` to the canonical `manual` (re-confirmed on claude 2.1.217). Not caused by auto mode; landed here to share the review + smoke.
- Specs: `exec-task-loop` modified (gate modes, outbound media); `agent-trust-layers` modified (auto tier posture: whitelist requirement + evaluator-as-merge-trigger residual).
- Depends on: PR #20 (fix/job-loss-family) and PR #21 (egress allowlist A) — both already merged to `main` (see tasks §0).
- Security review gate required（涉及權限/外部輸出）：auto-merge 是把「人審」換成「verify+evaluator 雙訊號」，SECURITY.md 需新增一節姿態說明（含 verdict-injection 殘留與雙訊號相關性）；outbound attachment 是新的資料外流面（受白名單/caps/`is_relative_to` 路徑限制、operator-only channel 界定機密性）。
