# Tasks — unattended auto mode + outbound media

## 0. Preconditions
- [ ] 0.1 PR #20 (fix/job-loss-family) merged — commit semantics the chain depends on
- [ ] 0.2 PR #21 (egress allowlist A) merged — WebSearch/doc lookups for the verify tier.
      NOTE (updated 2026-07-21, superseding the original "needs a registry mirror first"
      note; source: `registry-egress-opt-in`, PR #23, archived):
      - **Python: available today.** `pypi.org` + `files.pythonhosted.org` are reachable
        on the **executor** proxy when the operator opts in at build time
        (`EXTRA_FILTER=filter.pypi`). Default build is off and byte-identical, so auto
        mode may assume pip reachability *only* where the operator enabled it.
      - **npm: still unreachable, and a mirror is NOT the unlock.** A read-only mirror
        (Verdaccio et al.) was explicitly ruled out. The unlock condition is a
        **credential-free build container** — move installs out of the container holding
        the OAuth credential. Track separately; do not assume npm reachability here.
      - **Why npm and not PyPI:** the test is per host — is this host *also* a write
        endpoint? npm's publish endpoint is the same host as its registry, and the proxy
        is CONNECT-only (no TLS bump), so reachable means arbitrary method and body.
        PyPI splits them (uploads live on `upload.pypi.org`), so its two read hosts pass.
        "The container holds credentials" is NOT the criterion — an injected dependency
        can carry its own token.
      - **Residual risk to carry into the auto gate:** verify runs
        `pip install -e . && pytest`, and pytest imports installed packages, so a
        typosquat executes inside the credential-holding executor with no prompt
        injection required. See `SECURITY.md` §6.

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
