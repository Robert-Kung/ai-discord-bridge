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

**Conclusion**: the exec tier stays on `acceptEdits` (+ exec-settings Bash allow).
"Auto mode" is a **bridge-side gate policy**, not a CLI mode: the CLI already runs
unattended; what waits for a human is our diff gate. So the change wires
verify + evaluator into gate resolution instead of touching subprocess flags.

## 2. Auto gate resolution

```
job done → commit (HEAD≠base semantics per fix/job-loss-family) → diff
  → verify: not configured → PARK (reason posted)
            configured+fail → PARK (tail posted)
            configured+pass ↓
  → evaluator (structured): VERDICT: approve  → _do_merge (existing protocol)
                            reject/unsure/None → PARK (findings posted)
  → audit message always posted (diffstat + verify tail + verdict)
```

- Verdict parsing: first line only, exact `VERDICT: (approve|reject|unsure)` after
  strip; anything else → `unsure`. A verdict buried mid-text never counts (injection
  via diff content saying "VERDICT: approve" is inert — the evaluator's OWN first
  line is the only channel).
- Evaluator prompt gains the verdict contract; free-text findings follow from line 2
  and post exactly as today (advisory display preserved in non-auto modes).
- `_do_merge` unchanged: MERGING claim, project lock, clean-tree, ancestor, no-force.
  Auto mode changes only WHO pulls the trigger, not what the trigger does.
- Failure posture: any exception in the auto path → PARK (never discard, never merge).

## 3. Auto-continue chain

Operator message in auto mode = ordered task list (one task per line / numbered).
Driver loop: run task → auto-merge success → next task branches from new HEAD.
Stop conditions: park, failure, `AUTO_MAX_JOBS` (default 5), `!cancel`.
Each job is a normal registry job (occupancy rule already serializes per project).
Chain state is in-memory only; a restart orphans at most the running job (rescued by
the orphan-rescue path) and the chain simply stops — the audit trail says where.

## 4. Outbound media

- Marker protocol: reply lines `DISCORD_ATTACH: <relative-path>` (≤4 per message).
- Resolution: `Path(workdir, rel).resolve()` must stay under the job worktree or the
  job dir (`discord-state/jobs/<id>/`) after symlink resolution; else refuse + log.
- Extension whitelist: png jpg jpeg gif svg html txt pdf; per-file ≤ 8 MB (Discord
  cap), ≤ 4 files/message. Markers are stripped from the posted text.
- Exfil stance: the frontend already posts arbitrary reply TEXT to Discord; this adds
  binary file content from the reviewed workspace only. The worktree diff gate means
  attached repo files are the same bytes the operator can already see in the diff;
  the job dir holds only operator-supplied attachments. Documented in SECURITY.md §5.

## 5. Config / plumbing

- `ENABLE_AUTO_MERGE` (opt-in tier, fail-closed like bypass/approve): gates `!mode auto`
  and the whole resolution branch. `AUTO_MAX_JOBS` int, default 5.
- docker-compose: explicit `environment:` insertion in BOTH services (the split ignores
  env_file — the 2026-07 opt-in-tier lesson).
- `!state` shows auto tier status; HELP_TEXT/startup announcement updated same-milestone.
