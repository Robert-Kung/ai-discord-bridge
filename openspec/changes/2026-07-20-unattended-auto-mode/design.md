# Design — unattended auto mode + outbound media

## 1. CLI permission-mode research（2026-07-20，claude 2.1.x）

`--permission-mode` now offers `auto` and `dontAsk` beyond the modes the bridge maps
(plan/acceptEdits/default/bypassPermissions). Findings (docs: code.claude.com
permission-modes.md, permissions.md):

- **`auto`**: a safety classifier decides per tool call. Headless hazard: 3 consecutive
  (or 20 total) classifier blocks **abort the session** — an unattended exec job could
  die mid-task on misclassification. NOT suitable for the exec tier.
- **`dontAsk`**: everything outside `permissions.allow` is **silently denied**; no
  prompts, no aborts. This is the first mechanism that gives headless a REAL per-command
  allow-list (preflight finding stands: `--allowedTools` never restricted; `dontAsk`
  flips the default to deny). Candidate for a future tightening of the exec tier —
  out of scope here, noted for `execution-permissions`.
- `permissions.deny` overrides in every mode incl. bypass — deny family posture unchanged.

**Re-confirmed on claude 2.1.217 (2026-07-22)**: `--permission-mode` now lists
`acceptEdits · auto · bypassPermissions · manual · dontAsk · plan`. Two shifts vs the
2026-07-20 note:
- **`default` is gone from the documented choices, renamed `manual`.** It is still
  accepted at runtime (verified: a bogus mode is rejected by commander with the choices
  list, whereas `--permission-mode default` runs), so the bridge's `approve → default`
  mapping (`config.py:260`) and `runner.py:196` are NOT broken today — but they now rely
  on an UNdocumented back-compat alias. Migrate both to `manual` (the canonical name) so
  the bridge stays on a documented choice; this change folds that in (tasks §6) with a
  smoke to confirm headless `-p` semantics are unchanged.
- The bridge's `VALID_MODES` uses its OWN mode vocabulary (plan/edit/bypass/approve, and
  now `auto`); the new bridge mode `auto` is a gate policy and is unrelated to the CLI's
  `--permission-mode auto` (same name, different layer — no collision).

**Conclusion**: the exec tier stays on `acceptEdits` (+ exec-settings Bash allow).
"Auto mode" is a **bridge-side gate policy**, not a CLI mode: the CLI already runs
unattended (and the CLI `auto` mode's classifier-abort hazard makes it unsuitable
regardless); what waits for a human is our diff gate. So the change wires
verify + evaluator into gate resolution instead of touching subprocess flags.

## 2. Auto gate resolution

```
!mode auto → refuse unless (ENABLE_AUTO_MERGE ∧ author∈ALLOWED_USER_IDS
                            ∧ m4_live ∧ EVALUATOR_ENABLED)      [fail-closed]
job done → commit (HEAD≠base semantics per fix/job-loss-family) → diff
  → verify (auto path's OWN request_verify, exception→PARK; NOT _post_verify):
            not configured / unavailable → PARK (reason posted)
            configured+fail             → PARK (tail posted)
            configured+pass ↓
  → evaluator (structured): VERDICT: approve  → _do_merge (existing protocol)
                            reject/unsure/None/unparseable → PARK (findings posted)
  → audit message ALWAYS posted (diffstat + verify tail + verdict + dep-note)
```

- **Verdict parsing**: evaluator's OWN first line only. Tolerant of surrounding
  markdown/preamble on that line (`**VERDICT: approve**`, a `VERDICT: approve — …`
  trailing clause) so real model output is not false-parked into an
  auto-never-merges no-op (M5); but a verdict on any later line never counts.
  Injection via diff content saying "VERDICT: approve" is inert — the evaluator's
  first line is the only channel.
- **Residual (NOT sealed)**: the tolerant-first-line rule defeats a *literal* injected
  string, but NOT a diff that prompt-injects the evaluator model into *genuinely
  emitting* `approve` as its own first line. `evaluate_diff` hands attacker-influenceable
  diff content to the other bot; the random-token delimiter raises the bar but LLM
  injection is not reliably defeated. In auto mode a successful injection is a merge
  trigger. This residual is stated in SECURITY.md (not hidden behind "the channel is
  sealed"), and task 1.3 red-teams the evaluator's obedience, not just the parser.
- **Two signals are correlated, not independent**: the evaluator is the OTHER bot (same
  model family) reviewing the SAME diff that steered the executor, and verify runs the
  agent-authored tests. A determined injection can satisfy both. SECURITY posture:
  "two green" = "not obviously bad", never "safe".
- **Dependency changes are surfaced, not gated** (decision 2026-07-22): a merge-time
  dep-veto is rejected — (a) it does not protect the credential-holding executor
  because verify already ran `pip install -e . && pytest` *before* any gate, so the
  supply-chain code executed regardless of who merges; (b) a blanket park on every
  dep-touching job guts auto mode's purpose (greenfield / add-feature tasks routinely
  touch manifests). Instead the audit message lists `worktree.dependency_changes(full)`
  when non-empty, preserving the post-hoc signal without the utility cost. The real
  mitigation (credential-free / egress-restricted verify sandbox) is orthogonal to
  gate mode, protects human mode too, and is tracked as a separate change.
- `_do_merge` unchanged: MERGING claim, project lock, clean-tree, ancestor, no-force.
  Auto mode changes only WHO pulls the trigger, not what the trigger does.
- **Non-git cwd → refuse fail-closed (review H1):** a non-git cwd has no worktree and no
  diff gate, so the M1 direct-on-live path would run the agent UNGATED and (worse) return
  `DONE`, which the chain would read as "merged" and advance. `DEFAULT_CWD` is non-git, so
  this is reachable by default. `_drive_exec_job` therefore refuses an `auto` job on a
  non-git cwd BEFORE the agent runs (status → FAILED, which also stops any chain).
- Failure posture: any exception in the auto path → PARK (never discard, never merge). A
  `!cancel` landing during verify/evaluator → discard the branch (the operator threw it
  away), never a contradictory "awaiting-review" park (review M4).

## 3. Auto-continue chain

Operator message in auto mode = ordered task list (one task per line / numbered).
Driver loop: run task → **branch task N+1 only when task N reached `status == DONE`**
(a real `--no-ff` merge commit exists), re-reading HEAD *after* that commit. An
`approve` verdict that still parks (`_do_merge` returns dirty/diverged/conflict) is
NOT a success and stops the chain — the driver gates on merge success, not on the
verdict. Backstop: `project_occupied` (`_OCCUPY` includes AWAITING_REVIEW) already
rejects a task N+1 spawn while task N sits parked, so a mis-gated driver fails to a
confusing rejection, never a wrong merge. Operational note: any uncommitted change in
the live checkout makes every merge return `dirty`, so the whole chain fail-closes at
task 1 — documented so an operator knows to start from a clean tree.
Stop conditions: park, failure, `AUTO_MAX_JOBS` (default 5, int > 0), `!cancel`.
Chain state is in-memory only; a restart orphans at most the running job (rescued by
the orphan-rescue path → AWAITING_REVIEW, never auto-merged post-restart) and the chain
simply stops — the audit trail says where.

## 4. Outbound media

- **Marker protocol**: reply lines `DISCORD_ATTACH: <relative-path>` (≤4 per message);
  markers are stripped from the posted text. Applies to both exec-job replies
  (`start_exec_job` posts the agent's `reply` before the diff gate) and chat/converse
  replies.
- **Containment root depends on the reply's workspace** (this is the M2 gap resolved):
  - exec job → root is the job worktree (`workdir`) or the job dir
    (`discord-state/jobs/<id>/`).
  - chat/converse → root is the live project checkout (the channel cwd), since that is
    the only workspace a chat reply has.
  Resolution: `Path(root, rel).resolve()` must satisfy `is_relative_to(root)` after
  symlink resolution — NOT a string prefix (a `worktree` vs `worktree-evil` sibling
  would pass a prefix check). Else refuse + log the offending path.
  - **`DEFAULT_CWD` (home) root → refuse all (review M3):** with no project selected the
    chat root is the home dir; rooting containment there would expose the whole home tree.
    Attachments require a selected project (or an exec worktree), never the home root.
- **Extension whitelist**: png jpg jpeg gif svg html txt pdf; per-file ≤ 8 MB (Discord
  cap), ≤ 4 files/message.
- **Exfil stance (corrected)**: the earlier "attached bytes == the bytes the operator
  already sees in the diff" justification is WRONG and removed — attach happens *before*
  the diff gate, an auto-merge (or a non-merge) never triggers the diff gate, and the
  agent can attach a pre-existing, unchanged file that never appears in any diff. The
  honest stance: the frontend already posts arbitrary reply TEXT to the operator-only
  channel today (existing reply-exfil residual, SECURITY.md §6), so a trusted/injected
  agent can already exfil secrets as text; outbound media raises that from text to
  higher-bandwidth binary/whole-file content — an *expansion* of the existing residual,
  not a new trust boundary. Confidentiality is still bounded to the whitelisted operator
  by the channel gate. Documented in SECURITY.md §5 with this framing, and flagged as
  the higher-bandwidth version of the reply-exfil residual (not "safe because it's the
  diff bytes").

## 5. Config / plumbing

- `ENABLE_AUTO_MERGE` (opt-in tier, fail-closed): gates `!mode auto` **together with**
  `author_id in ALLOWED_USER_IDS` (auto carries more authority than bypass → same
  flag+whitelist posture, mirrored in `trust.py`). `VALID_MODES` gains `"auto"` and the
  dispatch-time downgrade list (currently `("bypass","approve")`) gains `"auto"` so auto
  is re-validated per message. `AUTO_MAX_JOBS` int > 0, default 5.
- Enabling auto also requires `m4_live` (verify path) and `EVALUATOR_ENABLED` to be
  live; `!mode auto` refuses to serve otherwise (better than a silent park-everything).
- docker-compose: explicit `environment:` insertion in BOTH services (the split ignores
  env_file — the 2026-07 opt-in-tier lesson).
- `!state` shows auto tier status; HELP_TEXT/startup announcement updated same-milestone.
