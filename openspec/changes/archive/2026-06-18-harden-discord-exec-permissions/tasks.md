## 0. Preflight

- [x] 0.1 **Design gate (OV2)** — DONE, see `preflight-findings.md`. claude **2.1.179**. Empirical verdict: headless `-p` **defaults to allow** for Bash; **`--allowedTools` is additive, NOT restrictive** (non-allow-listed `whoami` runs anyway); **`permissions.deny` enforces deterministically**. D1 revised: containment = `permissions.deny` + plan-default; restrictive allow-list deferred to M4 approver.
- [x] 0.2 **Hard gate (OV5)** — DONE, see `preflight-findings.md`. bwrap sandbox **cannot start in the container** (bwrap absent + unprivileged userns EPERM under Docker seccomp/caps). Fallback chosen (operator, option A): **accept "no OS layer"**, `sandbox.enabled:false`, document residual risk in SECURITY.md. No silent degrade.
- [x] 0.3 Allow-list **collected** (2026-06-18) → `command-allowlist.md` (pytest / read-only git / build+lint; operator "pytest etc." + suggested defaults). It is the M4 approver's auto-allow policy (the restrictive allow-list gate 0.1 showed `--allowedTools` can't enforce). M4 preflight done → `m4-approver-preflight.md`: approver enforces deny, schema captured, but **must run under `--permission-mode default` (NOT acceptEdits, which bypasses it)**.

## 1. M0 — Template + identity (zero DC interruption)

- [x] 1.1 Fix `docker-compose.example.yml`: remove the "even bypass mode can't touch it" claim adjacent to credential mounts; add a note flagging the mounted `~/.claude*` as live credentials reachable by the execution path (spec: bot-identity-isolation)
- [x] 1.2 Introduce a dedicated minimal `CLAUDE_CONFIG_DIR` for the bots (A1a): minimal `CLAUDE.md` with no operator personal data and **no `@import` of the shared `CLAUDE.md`**; point `BOT_CONFIG_DIRS` at the new `~/.claude-bot-{a,b}` dirs
- [x] 1.2a **(Issue 1)** Compose: REMOVE the wholesale `~/.claude` and `~/.claude-b` mounts (`docker-compose.yml:15-16`); mount only the new `~/.claude-bot-{a,b}` dirs. Single-file bind-mount each `.credentials.json` into the bot dir (no re-login, same billing) so symlinked creds resolve without exposing the whole operator account dir (spec: bot-identity-isolation)
- [x] 1.3 Narrow the `.claude-shared` mount to an explicit allow-list: mount `discord-state/`, `discord-summaries/`, `discord-project-notes/`, the plan landing zone `plans/`, and the single file `memory/project_plan.md`; stop mounting the `.claude-shared/memory/` directory and the shared `CLAUDE.md` (spec: bot-identity-isolation)
- [x] 1.4 Conversation-layer persistence stays on the harness path (`bot.py` flush → `save_summary`/`save_project_notes` → `discord-*`), which already snapshot-rotate the prior version (no free-form clobber). **REVISED (code review):** the speculative `save_plan`/`append_plan_index` helpers were dropped as dead code — `append_plan_index` would in fact fail against the `:ro` project_plan.md mount. The no-clobber guarantee for the operator index is the **read-only mount** (execution path structurally cannot overwrite it), stronger than an in-process helper (spec: agent-trust-layers)
- [x] 1.5 Clarify the operator-side `~/.claude-shared/CLAUDE.md` plan-placement rule (out of repo): full plans → `.claude-shared/plans/<slug>.md` (or a linked `memory/plan_*.md` detail file), linked from `project_plan.md`; `project_plan.md` stays a thin summary+links index; `~/.claude-b/plans/` only for throwaway single-account drafts
- [x] 1.6 Condense `~/.claude-shared/memory/project_plan.md` to the summary rule: per-project status + one-line summary + link to the full plan (`plans/`, `~/openproject-stack/docs/PLAN.md`, `SECURITY_REMEDIATION_PLAN.md`, etc.); move inline operational detail (SMTP relay names, domains, security-hole references) into those linked files so the single-file-mounted index carries no `🔴` content
- [x] 1.7 Test: loaded `CLAUDE.md` chain for a bot call contains no operator personal data, excludes the shared `CLAUDE.md` import, and the bot dir is not the operator primary dir (spec: bot-identity-isolation)
- [x] 1.8 Test: the execution path can read `memory/project_plan.md` but `memory/infrastructure.md` (and other siblings) are not present in the container; a newly added `memory/` file is not reachable without an explicit single-file mount (spec: bot-identity-isolation)
- [x] 1.9 Test **(Issue 1)**: the operator's `~/.claude` and `~/.claude-b` dirs are NOT mounted into the execution container; the bot dir is `~/.claude-bot-{a,b}` and its `.credentials.json` resolves inside the container (spec: bot-identity-isolation)

## 2. M1 — settings.json: deny family + sandbox

- [x] 2.1 Add a repo-tracked server-side `settings.json` with `permissions.deny` (credential reads, `env`, `printenv*`, `curl`, `wget`, `WebFetch`). **(Issue 5)** This is the single source for credential-read denial: REMOVE `CREDENTIAL_DENY_TOOLS` / the `--disallowedTools` credential family from `bot.py` (the blanket `Read(//home/user/.claude-shared/**)` also wrongly blocked the project_plan.md read — Issue 2 — so dropping it resolves that too)
- [x] 2.2 **REVISED (gate 0.2):** set `sandbox.enabled: false` in `settings.json` (bwrap can't start in the container — decision A, no silent degrade). Keep `permissions.deny` as the working control. Document the absent OS layer + residual risk in SECURITY.md (task 6.2). The credential/memory `filesystem.denyRead` set is recorded as a comment/future-work block for when the sandbox is restored (option B).
- [x] 2.3 Wire `--settings <path>` into the `claude -p` argument assembly with **(Issue 4)** a fixed, tested argument order: value flags first (`--settings`, `--permission-mode`), the variadic flag (`--disallowedTools …`) last, prompt via stdin. (No `--allowedTools` — gate 0.1 showed it doesn't restrict; deferred to M4.)
- [x] 2.4 **(OV1)** Add a runtime settings canary: on startup (or pre-call) the bot proves `--settings` actually loaded — e.g. a must-be-denied canary action that has to be refused — because `claude` *silently ignores* a settings file that fails validation. If the canary is not denied, fail closed (refuse to start / refuse the call)
- [x] 2.5 **(OV4)** Specify the bypass-UX transition for M1–M3: `bypassPermissions` becomes an opt-in tier off by default; the existing `do_plan_then_execute` / `!yolo` (bot.py:777) remains the gate for that tier *only while it is explicitly enabled*, until the M4 MCP approver replaces it. No dead code, no ambiguous lifecycle
- [x] 2.6 Test: credential read and env dump are denied; deny holds even when an invocation runs in bypass (spec: execution-permissions)
- [x] 2.7 **REVISED (gate 0.2):** Test that the sandbox is *explicitly disabled* in `settings.json` (`sandbox.enabled is False`) — i.e. no ambiguous "on but silently absent" state. (Fail-closed-on-unavailable is moot since the sandbox can't run here; the explicit-off assertion is the no-silent-degrade guarantee.) (spec: execution-permissions)
- [x] 2.8 Test **(OV1)**: when `settings.json` is corrupted/unloadable, the canary trips and the bot fails closed (does not run with silently-dropped deny/sandbox) (spec: execution-permissions)
- [x] 2.9 Test **(Issue 4)**: the assembled `argv` has the documented order; neither variadic flag swallows the other or the prompt (spec: execution-permissions)
- [x] 2.10 Test **(Issue 5, regression)**: migrate the existing credential-deny test from the `CREDENTIAL_DENY_TOOLS` path to assert the `settings.json` deny — do not delete coverage when removing the old mechanism (spec: execution-permissions)

## 3. M1 — allow-list replaces full bypass

- [x] 3.1 **REVISED (gate 0.1):** Execution path uses `--permission-mode acceptEdits` (the `edit` tier) contained by the `permissions.deny` family — NOT `--allowedTools` (which doesn't restrict). `bypassPermissions` is no longer a default. The restrictive per-command allow-list is the M4 approver.
- [x] 3.2 Make full `bypassPermissions` an explicit opt-in tier, off by default; keep default channel mode at the safe read/plan mode
- [x] 3.3 **REVISED (gate 0.1):** Test the deny family bites under the execution mode — a denied command (credential read / `printenv` / `curl`) does not execute under `acceptEdits`; the deny set is what contains execution (there is no allow-list restriction to assert). (spec: execution-permissions)
- [x] 3.4 Test: bypass is unreachable without explicit opt-in; default channel mode is the safe default (spec: execution-permissions)

## 4. M3 — Two-layer split + single chokepoint

- [x] 4.1 **(Issue 3 — reframed)** The single chokepoint already exists (`call_claude`, bot.py:464, is the sole funnel). The M3 work is to remove `mode` as a free caller parameter: conversation entry points call a narrow wrapper that hard-codes plan/read and is structurally unable to pass `acceptEdits`/`bypassPermissions`/`--allowedTools`; only the post-auth execution entry can request write/execute
- [x] 4.2 Separate the conversation layer (A↔B debate, summaries, memory) from the execution layer; conversation layer calls a narrow entry that hard-codes read/plan mode and cannot pass execution flags
- [x] 4.3 Route the human-driven execution layer through the chokepoint after the user/channel authorization checks
- [x] 4.4 Test: only one function constructs/launches the subprocess (spec: agent-trust-layers)
- [x] 4.5 Test: conversation-layer entry points never emit `acceptEdits`/`bypassPermissions`/`--allowedTools` for any input (spec: agent-trust-layers)
- [x] 4.6 Test: a non-whitelisted user cannot produce an execute-capable invocation (spec: agent-trust-layers)
- [x] 4.7 Test: conversation-layer plan/decision output is persisted by the harness (mode-independent), with no plan-mode subprocess file write (spec: agent-trust-layers)
- [x] 4.8 Test: no bot code path invokes `sibling`; inter-agent discussion goes over Discord `@`-mention (spec: agent-trust-layers)

## 5. M4 — Optional per-command approver

- [x] 5.1 `mcp_approver.py` — standalone MCP stdio approver (no deps) + pure `approver_policy.py` (shared, no discord import) + `approver-allowlist.json`. Auto-allows allow-listed tools/commands, escalates the rest to the bridge over a unix socket → `request_discord_approval` posts to Discord (✅/❌, whitelist-gated via the existing reaction handler). Fail-closed on no-socket/timeout/malformed.
- [x] 5.2 Wired into the chokepoint: opt-in `ENABLE_APPROVER_TIER`; new `approve` mode → `build_claude_args` adds `--permission-prompt-tool mcp__approver__approve --mcp-config … --strict-mcp-config` and forces `--permission-mode default` (acceptEdits bypasses the approver — verified); `--settings` deny family still applied. Default-closed (`approve_allowed`, downgrade-to-plan, cmd_mode gate).
- [x] 5.3 Tested: `test_approver.py` (20 tests) — policy allow/escalate incl. compound-segment safety; MCP `evaluate` allow/deny incl. fail-closed (no socket / socket drop); full JSON-RPC protocol over a real subprocess; with approver off `--settings` (deny/sandbox) still applied. Plus a live e2e probe (deny propagated, file not created — see `m4-approver-preflight.md`) (spec: execution-permissions)

## 6. Wrap-up

- [x] 6.1 Run the full pytest suite; confirm fail-closed guarantees green
- [x] 6.2 Update `SECURITY.md` to reflect the new execution model (allow-list + sandbox + deny; bypass opt-in)
- [x] 6.3 Update `docs/SECURITY_REMEDIATION_PLAN.md` milestone status
</content>
