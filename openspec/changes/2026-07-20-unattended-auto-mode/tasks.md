# Tasks — unattended auto mode + outbound media

## 0. Preconditions
- [x] 0.1 PR #20 (fix/job-loss-family) merged — commit semantics the chain depends on
      (in `main` as eadecf1).
- [x] 0.2 PR #21 (egress allowlist A) merged — WebSearch/doc lookups for the verify tier
      (in `main` as ddf2220).
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
      - **Residual risk carried into the auto gate (decision 2026-07-22):** verify runs
        `pip install -e . && pytest` and pytest imports installed packages, so a typosquat
        executes inside the credential-holding executor with no prompt injection — AND
        this happens at verify time, *before* any gate, so it is identical in human and
        auto mode. Auto mode does NOT amplify this execution (only its unattended
        frequency), so no dep-veto is added; the risk is documented-accepted in
        SECURITY.md §6 and the real fix (credential-free verify sandbox) is a separate,
        higher-priority change. Dep changes ARE surfaced in the audit message (§2.3).

## 1. Structured evaluator verdict (bridge/discuss.py)
- [x] 1.1 Verdict contract in the evaluator prompt (first line `VERDICT: …`)
- [x] 1.2 `parse_verdict(text) -> "approve"|"reject"|"unsure"` — evaluator's OWN first
      line only, tolerant of surrounding markdown/preamble (`**VERDICT: approve**`,
      trailing clause), default unsure; a verdict past line 1 never counts
- [x] 1.3 Tests: parse matrix incl. (a) adversarial buried/injected verdict lines,
      (b) realistic well-formed markdown/preamble approval (must NOT false-park),
      (c) an LLM-obedience red-team — a diff crafted to steer the evaluator into
      emitting `approve` as its first line (documents the residual, not just the parser),
      (d) empty/None findings
      （tests/test_auto_mode.py::test_parse_verdict_*）
- [x] 1.4 doc-delta: SPEC §6.5 evaluator verdict + verdict-injection & correlation
      residual（zh 同步：SPEC 本文即 zh）

## 2. Auto gate (bridge/frontend.py + config + trust)
- [x] 2.1 `ENABLE_AUTO_MERGE` + `AUTO_MAX_JOBS` (int > 0) in config; `VALID_MODES += "auto"`;
      dispatch-time downgrade list gains `"auto"`; compose env (frontend service, explicit
      environment — auto is a frontend gate policy like bypass/approve/evaluator, so it
      lives beside them on the frontend, not both services) + .env.example
- [x] 2.2 `trust.py`: `!mode auto` honoured only with `ENABLE_AUTO_MERGE` AND
      `author_id in ALLOWED_USER_IDS` (mirror bypass/approve); refuse-to-serve when
      `m4_live` or `EVALUATOR_ENABLED` is unavailable（`auto_allowed` + `auto_preconditions_ok`;
      cmd_mode gives the specific refusal reason）
- [x] 2.3 Gate resolution branch per design §2: auto path runs its OWN `request_verify`
      (exception→park; do NOT reuse `_post_verify`), tolerant verdict parse, dep-note
      appended to the audit message from `worktree.dependency_changes(full)`,
      park-on-anything-unclear（`_resolve_auto_gate`）
- [x] 2.4 Tests: refuse-to-serve on missing flag/whitelist/verify/evaluator;
      park-on-unverified / park-on-reject / park-on-evaluator-unavailable /
      merge-on-approve (stub verify+evaluator); dep-note present when deps change
- [x] 2.5 doc-delta: SPEC §6.6 gate modes、SECURITY.md+zh §4 auto tier posture（含
      whitelist 要求、verdict-injection 殘留、雙訊號相關性、dep 只 surface 不 gate）、
      README both

## 3. Auto-continue chain (bridge/frontend.py)
- [x] 3.1 Task-list splitter + chain driver: advance only on `status == DONE`
      (re-read HEAD after the merge commit); stop on park/fail/cap/cancel
      （`split_task_list` + `_run_auto_chain`）
- [x] 3.2 Tests: chain bounds (`AUTO_MAX_JOBS`), branch-from-new-HEAD, stop-on-park,
      approve-but-parked (dirty/diverged) does NOT advance
- [x] 3.3 doc-delta: HELP_TEXT + startup announcement (incl. "start from a clean live
      tree" operational note)

## 4. Outbound media (bridge/frontend.py)
- [x] 4.1 Marker parse + containment: exec reply → root = worktree/job dir; chat reply →
      root = live checkout; resolve with `Path.resolve()` + `is_relative_to` (NOT string
      prefix — call out the `worktree-evil` sibling trap) + whitelist/caps
      （`resolve_attachment_markers`）
- [x] 4.2 Attach via discord.File; strip markers from posted text (both exec + chat paths)
      （`_send_reply`, wired into the exec-job reply + the standard converse reply）
- [x] 4.3 Tests: traversal/symlink escape refusal using a **whitelisted-extension**
      target (so containment, not the extension check, is what refuses), extension/size/
      count caps, marker stripping, chat vs exec root selection
- [x] 4.4 doc-delta: SECURITY.md+zh §5 outbound surface（誠實標為既有 reply-exfil residual
      的高頻寬版、operator-only channel 界定機密性、跨容器讀取耦合）、README usage

## 5. `default` → `manual` permission-mode migration (bridge/config.py + runner.py)
- [x] 5.1 `config.py` `approve → "default"` becomes `approve → "manual"`;
      `runner.py` `build_claude_args("default", …)` becomes `"manual"`
      (re-confirmed on claude 2.1.217: `default` dropped from documented `--permission-mode`
      choices, `manual` is the successor; `default` still runs as undocumented back-compat)
- [x] 5.2 Grep the tree for any remaining `"default"`/`'default'` permission-mode literal;
      none survive outside comments/DEFAULT_* (grep clean)
- [ ] 5.3 Live smoke: the `approve` tier on `manual` behaves as it did on `default` in
      headless `-p` (semantics unchanged — this is the only thing static analysis can't prove)

## 6. Review gate
- [ ] 6.1 reviewer + security-reviewer on the full diff（auto-merge 決策面＋outbound 外流面；
      重點：H2 verdict-injection、雙訊號相關性、containment `is_relative_to`）
- [ ] 6.2 Live smoke: auto job with passing verify round-trips to merged; park paths
      (unverified / reject / evaluator-unavailable / refuse-to-serve) visible;
      dep-touching job auto-merges WITH a dep-note in the audit message
