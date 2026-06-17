## Context

`bot.py` is the entire implementation (~55KB, single file). Today the execution call is assembled once at the current `claude -p` call site and runs `--permission-mode <channel-mode>` where `bypass` maps to `bypassPermissions`, plus a `--disallowedTools` Read deny on the mounted config dirs. The Read deny is bypassable by shell (`cat`/`printenv`), and `~/.claude*` are mounted read-write. Authorization is a fail-closed `ALLOWED_USER_IDS` whitelist; bypass is whitelist-gated but otherwise unbounded.

Two decisions are fixed before this design: (1) auth stays on subscription OAuth — the 2026-06-16 billing retraction means `claude -p` keeps drawing from the subscription, so the `USE_API_KEY` dual-mode skeleton stays dormant and credential dirs remain mounted; (2) host-level remediation (firewall, port binding, docker group) is handled separately. Therefore credential protection must come from permission/sandbox layering, not from removing the mount.

## Goals / Non-Goals

**Goals:**
- Keep the bot executing real work in Discord (read, edit, allow-listed commands like tests/git/builds).
- Make "a channel user reads OAuth credentials or exfiltrates secrets" impossible at the tool layer, the OS layer, and via env — defense in depth, no single point.
- Turn "the conversation layer never executes untrusted commands" into a structural guarantee, not a review-time observation.
- Land incrementally with zero interruption to the live bridge.

**Non-Goals:**
- Auth migration (API key / apiKeyHelper / credit billing) — explicitly retained as subscription OAuth.
- Host network/identity remediation — tracked in `docs/m1-host-remediation.md`.
- Removing the credential mount.

## Decisions

**D1 — REVISED after preflight gate 0.1 (see `preflight-findings.md`).** The original
plan (`--permission-mode acceptEdits` + `--allowedTools` as a *restrictive* allow-list)
**does not hold in claude 2.1.179**: empirically, headless `claude -p` defaults to
**allow** for Bash, and `--allowedTools` is *additive* (pre-approve), not restrictive —
a non-allow-listed `whoami` runs anyway. Building containment on `--allowedTools` would
silently allow arbitrary commands.

The enforceable model (operator decision 2026-06-17, option A):
- **`plan` is the default channel mode** — read-only, no edits, no state-changing bash.
  This is the real containment for the conversation layer and untriggered channels.
- **`permissions.deny` (via `--settings`) is the working tool-layer control** — it
  enforces deterministically in headless (gate probe E), so it carries the credential /
  env / network exfil denial.
- **Execution (`edit` = acceptEdits, or opt-in full bypass) is whitelist-gated and
  contained by the deny family + mount isolation + dedicated dir**, NOT by an allow-list.
- **The true restrictive allow-list is deferred to the M4 MCP `--permission-prompt-tool`
  approver** — the only headless mechanism that can deny-by-default. Until M4, the
  `edit`/`bypass` tiers run with deny-family containment only; this is an explicit,
  documented residual (SECURITY.md). The concrete command allow-list (task 0.3) is
  collected when M4 is built, not now.

**D2 — Server-side `settings.json` via `--settings`, deny family (sandbox REVISED).** A
repo-tracked `settings.json` carries `permissions.deny` (credential reads, `env`/`printenv`,
`curl`/`wget`, `WebFetch`) — deny always wins, even under bypass (gate probe E confirms it
enforces in headless). The chokepoint appends `--settings <path>` to every call. This deny
family is the **single source** for credential-read denial — the prior `CREDENTIAL_DENY_TOOLS`
/ `--disallowedTools` mechanism in `bot.py` is removed (DRY; it also blanket-blocked the
`.claude-shared/**` read path that the single-file `project_plan.md` mount now needs).
*Alternative considered:* put deny rules only in the bot's `CLAUDE_CONFIG_DIR/settings.json`
— rejected because passing an explicit `--settings` keeps the security config in the repo,
reviewable and version-pinned, rather than depending on dir state.

**OS sandbox REVISED after preflight gate 0.2 (operator decision 2026-06-17, option A).**
Claude Code's bubblewrap sandbox **cannot start in the container** (bwrap not installed AND
unprivileged userns blocked by Docker's default seccomp/caps for the `1000:1000` user). With
`failIfUnavailable: true` the bot would fail *every* execution call. Decision: **accept "no OS
layer"** — `settings.json` sets `sandbox.enabled: false` (no silent-degrade ambiguity: the
sandbox is explicitly off, not "on but quietly absent"). Containment rests on `permissions.deny`
+ plan-default + whitelist + (future) M4 approver. The residual risk — deny-by-name is
bypassable via `/usr/bin/cu*rl`, `python -c`, `cat /proc/self/environ` — is documented honestly
in SECURITY.md rather than papered over. Restoring the OS keystone later requires the runtime
changes in `preflight-findings.md` (option B), tracked as future work, not this change.

> **Critical (OV1): `--settings` fails OPEN, silently.** `claude --help` states a settings file that fails validation is *silently ignored* with no error. A schema drift on a Claude Code upgrade would make the entire deny+sandbox config evaporate while the bot keeps running `acceptEdits`. `failIfUnavailable: true` only fails the *sandbox* closed, not settings-loading. Mitigation: a **runtime canary** — the bot proves a must-be-denied action is actually refused before trusting the call path; if not, it fails closed. This is what makes D2's fail-closed claim real rather than asserted.

**D3 — Single exec chokepoint + two layers.** Collapse all argument assembly into one function; the conversation layer calls a narrow entry that hard-codes the read/plan mode and cannot pass execution flags. This makes the structural guarantee testable: tests assert the conversation-layer entry points never emit `acceptEdits`/`bypassPermissions`/`--allowedTools`. *Alternative considered:* a runtime flag checked at call time — rejected; a structural split is provable, a flag is forgeable.

**D4 — Dedicated minimal config dir + narrowed mounts.** Point the bots at a config dir whose `CLAUDE.md` carries no operator personal data, is not the primary account, and does **not** `@import` the shared `CLAUDE.md` (the infra/topology source enters the bot through that import otherwise). Cheapest variant (A1a) symlinks existing credentials and only swaps the `CLAUDE.md`; this removes personal-data exposure with no re-login and same subscription billing. A disposable-account variant (A1b) additionally bounds token-leak severity at the cost of one login per bot — left as an operator choice, not required by this change.

The dir swap alone is insufficient: `.claude-shared` is mounted into the container and `.claude-shared/memory/` holds operator PII / infra topology / internal plans (`infrastructure.md`, `project_plan.md`, …). So the mount is narrowed to only the bot's operational state — `discord-state/`, `discord-summaries/`, `discord-project-notes/` — and `memory/` + the shared `CLAUDE.md` are not mounted into the execution container. **Confidentiality** (don't expose the trove to the exec path) and **continuity** (DC-written plans land in the shared `discord-*` dirs so the operator's CLI/standup reads them) are orthogonal cuts, both required; the M1 sandbox `denyRead` (D2) is the backstop if a path is mounted anyway. *Note:* `~/.claude/projects/-home-user/memory` is already a symlink to `.claude-shared/memory`, so curated plans are physically config-dir-independent — only the symlink/`MEMORY.md` index is per-dir plumbing to recreate in the bot dir.

**D6 — Conversation-layer persistence + inter-agent channel.** The A↔B conversation layer runs plan/read mode (D3) and therefore *cannot* write files via the agent subprocess — plan mode only touches the harness session-plan scratchpad. Plan/decision output is persisted by `bot.py` itself (the flush path → `save_project_notes` → `discord-project-notes/`, mode-independent), not by the subprocess. A full plan document the agent must author requires the `edit` tier; it is never expected from the default plan-mode path. Inter-agent discussion happens over Discord `@`-mention (the existing debate path), **not** `sibling` — `sibling` is an operator CLI tool, unavailable headless / in the container, and is not invoked from the bot. *Alternative considered:* grant the conversation layer write capability so it can persist plans directly — rejected; it would break the D3 structural guarantee that the conversation layer emits no write capability.

**D5 — Optional per-command approver (later tier).** `--permission-prompt-tool mcp__approver__ask` points at a small MCP server that posts each dangerous command to Discord and waits for a reaction, returning `{allowed, reason}`. This replaces the current cosmetic whole-plan ✅ with real per-command approval. Sequenced last (M4) because it needs a new MCP server; the allow-list/deny/sandbox already contain the risk without it.

## Risks / Trade-offs

- **Claude Code flag/setting names AND behavior are version-sensitive** → task 0.1 is a **design gate (OV2)**: don't just check flags exist, empirically verify that `acceptEdits` + `--allowedTools` actually refuses a non-allow-listed Bash command in headless `-p` (and whether it errors or silently no-ops). M1 is architected on that behavior; if it differs, the bot either leaks (silent allow) or goes mute on every task (silent deny). Verify before designing on it.
- **Sandbox availability in the container is the keystone (OV5)** → deny-by-name cannot beat shell (`/usr/bin/cu*l`, `python -c`, `cat /proc/self/environ`), so bubblewrap is the load-bearing OS layer. But bwrap in a container often needs userns / `CAP_SYS_ADMIN`. Task 0.2 is a **hard gate**: run a bwrap canary in the real image; if it can't start, fix the runtime config or accept "no OS layer" and tighten the allow-list — decided before M1, not discovered at runtime. `failIfUnavailable: true` fails the sandbox closed, but a hard gate prevents shipping a model whose keystone is absent.
- **`--settings` fails open silently (OV1)** → see D2. Mitigated by the runtime canary (tasks 2.4 / 2.8).
- **Allow-list too narrow → bot becomes less useful** → start from the operator's actual command set; expand deliberately. The opt-in bypass tier remains as an escape hatch for trusted, supervised sessions.
- **MCP permission-prompt-tool request/response schema is under-documented** → prototype and test the contract before depending on it (M4 only).
- **Credential file still present in container** (mount retained) → mitigated by D2 sandbox `denyRead` + deny rules + D4 dedicated dir; accepted residual is that a sandbox-and-deny bypass would be required to reach it.

## Migration Plan

1. **M0 (zero interruption):** fix `docker-compose.example.yml` (D-bot template), introduce the dedicated minimal config dir (D4 A1a), narrow the `.claude-shared` mount to the bot's `discord-*` state subdirs (D4), and confirm conversation-layer persistence stays on the harness path (D6). No change to live permission behavior.
2. **M1:** add `settings.json` (D2) and wire `--settings` into the chokepoint; switch the execution path from `bypassPermissions` to `acceptEdits` + `--allowedTools` (D1); default-close full bypass.
3. **M3:** refactor into the two-layer split with the single chokepoint (D3); add structural tests.
4. **M4:** add the optional MCP approver tier (D5).
- **Rollback:** each milestone is independently revertable; the chokepoint can fall back to the prior arg assembly. M1 is the only behavior-visible step for operators (bypass no longer default).

## Open Questions

- The concrete `--allowedTools` command allow-list — pending operator input (which commands the bot runs in Discord).
- Whether to adopt the disposable-account variant (A1b) now or defer.
- Exact sandbox `network` policy (full deny vs allow a registry/git host) — depends on whether allow-listed commands need network (e.g. `npm install`).
</content>
