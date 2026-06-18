## Why

The Discord-driven execution path runs `claude -p --permission-mode bypassPermissions` gated only by a user whitelist, while `~/.claude*` credential dirs are mounted read-write into the container. A whitelisted (or compromised) channel user can therefore shell out to read OAuth credentials (`cat`) or exfiltrate secrets (`printenv`, `curl`) — the Read-tool deny is trivially sidestepped by bypass-mode shell. The public release reviewed the repository for publication but never hardened the running deployment; this change closes the execution surface while keeping the bot able to do real work in Discord.

## What Changes

- **BREAKING (operational default):** full `bypassPermissions` is no longer the execution mode. The DC execution path runs `acceptEdits` + an explicit `--allowedTools` command allow-list; full bypass becomes an opt-in tier, off by default.
- A server-side `settings.json` is passed via `--settings` on every execution call, carrying a `permissions.deny` family (credential reads, `env`/`printenv`, `curl`/`wget`, `WebFetch`) that is enforced even if a call ever runs in bypass, plus an OS sandbox (Linux bubblewrap) that denies reads of credential files and restricts network.
- The bot↔bot conversation path and the human-driven execution path are separated into two trust layers; all `claude -p` argument assembly collapses into a single permission/exec chokepoint. The conversation layer is structurally incapable of emitting execution permissions.
- The bots run under a dedicated, minimal `CLAUDE_CONFIG_DIR` so the operator's personal `CLAUDE.md` (email/infra) and primary account are not in the blast radius. The minimal `CLAUDE.md` carries no operator PII and does **not** `@import` the shared `CLAUDE.md` (the infra/topology source); credentials are symlinked from the existing dir (no re-login, same subscription billing).
- **Mount granularity is narrowed:** the execution container mounts only the bot's own operational state (`discord-state/`, `discord-summaries/`, `discord-project-notes/`), not `.claude-shared/memory/` (the operator PII/infra/plan trove) nor the shared `CLAUDE.md`. Otherwise a bypass/injection could `cat .claude-shared/memory/infrastructure.md` and defeat the dedicated-dir benefit — confidentiality (don't mount the trove) and continuity (plans persist to the shared `discord-*` dirs) are treated as separate, both-required cuts.
- **Conversation-layer plan/decision output is persisted by the harness (`bot.py`, mode-independent), never by the plan-mode agent subprocess** (plan mode cannot write files; it can only touch the harness session-plan scratchpad). A full plan document that the agent itself must author requires the `edit` tier — it is not expected from the default plan-mode path.
- **The bot↔bot conversation layer discusses via Discord `@`-mention, not the operator-only `sibling` CLI** (unavailable headless / in the container). `sibling` stays a CLI-only operator tool and is not invoked from the bot.
- `docker-compose.example.yml` is corrected: the credential mount is documented as the highest-value secret, and the misleading "even bypass mode can't touch it" comment is removed.
- (Later tier) an optional `--permission-prompt-tool` MCP approver routes each dangerous command to Discord for real per-command approval, replacing the cosmetic whole-plan ✅.

Out of scope: auth migration to API key / apiKeyHelper / credit billing (the 2026-06-16 billing retraction keeps `claude -p` on subscription OAuth; the `USE_API_KEY` skeleton stays dormant). Host-level remediation (firewall, port binding, docker group, token rotation) is tracked separately in `docs/m1-host-remediation.md`.

## Capabilities

### New Capabilities
- `agent-trust-layers`: separation of the bot↔bot conversation layer (no execution capability) from the human-driven execution layer, with a single permission/exec chokepoint and a structural guarantee that the conversation layer cannot emit write/execute permissions. Includes the conversation layer's inter-agent channel (`@`-mention, not `sibling`) and the rule that its plan/decision output is persisted by the harness, not the plan-mode subprocess.
- `execution-permissions`: the permission model for the execution path — allow-listed command execution, enforced deny rules, OS sandbox, a default-closed full-bypass tier, and an optional per-command human-approval tier.
- `bot-identity-isolation`: the bots authenticate under a dedicated minimal config dir (no operator personal data / primary account, no shared `CLAUDE.md` import), the execution container mounts only the bot's operational state subdirs (not the `.claude-shared/memory` PII/infra trove), and the public deployment template honestly represents the credential-mount risk.

### Modified Capabilities
<!-- none — no existing specs in openspec/specs/ -->

## Impact

- **Code:** `bot.py` (subprocess arg assembly around the current `claude -p` call site; channel-mode handling; config-dir constants), `docker-compose*.yml` (config-dir mounts + narrowed `.claude-shared` mount granularity), the new minimal bot `CLAUDE.md`, a new server-side `settings.json`, new `tests/` for the permission/layer guarantees.
- **Operator-side (out of repo):** the shared `~/.claude-shared/CLAUDE.md` plan-placement rule is clarified (full plans → `.claude-shared/memory/` + link from `project_plan.md` + `MEMORY.md` index; `~/.claude-b/plans/` only for throwaway single-account drafts). This rule governs the operator's interactive standup agents, not the bot — the bot's minimal `CLAUDE.md` does not import it.
- **Runtime behavior:** execution in Discord continues (read/edit/allow-listed commands); arbitrary shell, credential reads, and arbitrary network are blocked. Operators relying on full bypass must opt in.
- **Auth/billing:** unchanged — subscription OAuth retained; credential dirs remain mounted, so credential protection shifts onto sandbox/deny/dedicated-dir rather than removing the mount.
- **Dependencies:** relies on current Claude Code flags (`--allowedTools`, `--settings`, sandbox settings, `--permission-prompt-tool`); exact flag/setting names AND behavior are version-sensitive and must be verified against the installed `claude` version (tasks 0.1 design gate, 0.2 hard gate).

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | issues_open→folded | 10 issues, 0 critical gaps remaining |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | (no UI surface) |
| Outside Voice | Claude subagent | Independent 2nd opinion | 1 | issues_found | 5 added (codex not installed) |

Findings folded (all accepted): **Issue 1** remove wholesale `~/.claude(-b)` mounts + single-file cred; **Issue 2** subsumed by Issue 5; **Issue 3** reframe M3 task 4.1 to structural mode-split (chokepoint already exists); **Issue 4** fixed argv order + test; **Issue 5** consolidate credential deny into `settings.json`, remove `CREDENTIAL_DENY_TOOLS`; **OV1** runtime settings canary (`--settings` fails open silently); **OV2** task 0.1 upgraded to behavior design-gate; **OV4** specify `do_plan_then_execute`/`!yolo` transition; **OV5** task 0.2 upgraded to bwrap hard gate + fallback. Sequencing **OV3** considered — operator kept M0→M1→M3→M4.

- **CROSS-MODEL:** no tension — outside voice added findings (settings fail-open, bwrap keystone, sequencing, transition state, behavior-unverified) rather than disputing the review. Strongest additive: OV1 settings-fails-open-silently.
- **VERDICT:** ENG CLEARED — scope accepted as-is (no reduction), 10 findings folded into tasks/design, 0 critical gaps remain. Ready to implement starting M0.

NO UNRESOLVED DECISIONS
</content>
