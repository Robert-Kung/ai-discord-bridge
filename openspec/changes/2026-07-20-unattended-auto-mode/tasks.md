# Tasks — unattended auto mode + outbound media

## 0. Preconditions
- [ ] 0.1 PR #20 (fix/job-loss-family) merged — commit semantics the chain depends on
- [ ] 0.2 PR #21 (egress allowlist A) merged — WebSearch/doc lookups for the verify tier.
      NOTE: npm registry was dropped from #21 on the security review (credential-container
      exfil sink); Node projects' `npm install`/test in auto mode needs a read-only
      registry mirror first — track separately, do NOT assume npm reachability here.

## 1. Structured evaluator verdict (bridge/discuss.py)
- [ ] 1.1 Verdict contract in the evaluator prompt (first line `VERDICT: …`)
- [ ] 1.2 `parse_verdict(text) -> "approve"|"reject"|"unsure"` — first line only, default unsure
- [ ] 1.3 Tests: parse matrix incl. buried/injected verdict lines, empty/None findings
- [ ] 1.4 doc-delta: SPEC §6.2 evaluator paragraph（zh 同步：SPEC 本文即 zh）

## 2. Auto gate (bridge/frontend.py + config)
- [ ] 2.1 `ENABLE_AUTO_MERGE` + `AUTO_MAX_JOBS` in config (fail-closed defaults) + compose env ×2
- [ ] 2.2 `!mode auto` refusal path when tier off (mirror bypass tier tests)
- [ ] 2.3 Gate resolution branch per design §2 (park-on-anything-unclear; audit message)
- [ ] 2.4 Tests: park-on-unverified / park-on-reject / merge-on-approve (stub verify+evaluator)
- [ ] 2.5 doc-delta: SPEC §6.2 gate modes、SECURITY.md+zh §4 auto tier posture、README both

## 3. Auto-continue chain (bridge/frontend.py)
- [ ] 3.1 Task-list splitter + chain driver (stop on park/fail/cap/cancel)
- [ ] 3.2 Tests: chain bounds, branch-from-new-HEAD, stop-on-park
- [ ] 3.3 doc-delta: HELP_TEXT + startup announcement

## 4. Outbound media (bridge/frontend.py)
- [ ] 4.1 Marker parse + path containment (resolve under worktree/job dir only) + whitelist/caps
- [ ] 4.2 Attach via discord.File; strip markers from posted text
- [ ] 4.3 Tests: traversal/symlink escape refusal, extension/size/count caps, marker stripping
- [ ] 4.4 doc-delta: SECURITY.md+zh §5 outbound surface、README usage

## 5. Review gate
- [ ] 5.1 reviewer + security-reviewer on the full diff（auto-merge 決策面＋outbound 外流面）
- [ ] 5.2 Live smoke: auto job with passing verify round-trips to merged; park paths visible
