## ADDED Requirements

### Requirement: Execution mode is acceptEdits contained by the deny family (NOT an allow-list)
> Revised after preflight gate 0.1 (see `preflight-findings.md`): in headless `claude -p`
> (claude 2.1.179) `--allowedTools` is additive, NOT restrictive — a non-listed command
> runs anyway. A restrictive allow-list is therefore impossible at the flag layer here and
> is deferred to the per-command approver tier. Containment of the execution path comes
> from the `permissions.deny` family, not an allow-list.

The execution path SHALL run with `--permission-mode acceptEdits` and SHALL NOT pass an `--allowedTools` execution list (it does not restrict). Containment of what executes SHALL come from the enforced `permissions.deny` family. Full `bypassPermissions` SHALL NOT be the default execution mode. A restrictive per-command allow-list SHALL be provided only by the optional approver tier (below).

#### Scenario: Execution runs under acceptEdits without an allow-list flag
- **WHEN** the human-driven execution path invokes the agent
- **THEN** the invocation uses `--permission-mode acceptEdits` and emits no `--allowedTools` flag

#### Scenario: A denied command does not execute under the execution mode
- **WHEN** the agent attempts a command covered by the `permissions.deny` family (credential read, `env`/`printenv`, `curl`/`wget`) under `acceptEdits`
- **THEN** the command is denied and does not execute

#### Scenario: Restrictive command confinement is the approver tier's job
- **WHEN** true "only these commands may run" confinement is required
- **THEN** it is provided by the optional `--permission-prompt-tool` approver, not by `--allowedTools`

### Requirement: Enforced deny rules survive any mode
A server-side `settings.json` SHALL be passed via `--settings` on every execution call, containing a `permissions.deny` set that blocks credential reads (`~/.claude` config dirs), environment dumping (`env`, `printenv`), arbitrary network fetch (`curl`, `wget`, `WebFetch`). These deny rules SHALL be enforced even when an invocation runs in bypass mode.

#### Scenario: Credential read is denied
- **WHEN** the agent attempts to read a mounted credential file via any tool or shell command
- **THEN** the attempt is denied

#### Scenario: Environment dump is denied
- **WHEN** the agent runs `printenv` or `env`
- **THEN** the command is denied

#### Scenario: Deny holds under bypass
- **WHEN** an invocation runs in the opt-in full-bypass tier
- **THEN** the deny rules still block credential reads, env dumps, and arbitrary network fetch

### Requirement: OS sandbox is explicitly disabled, residual documented (no silent degrade)
> Revised after preflight gate 0.2 (see `preflight-findings.md`): Claude Code's bubblewrap
> sandbox cannot start in the container (bubblewrap absent + unprivileged user namespaces
> blocked by Docker's default seccomp/caps), so enabling it with `failIfUnavailable: true`
> would make the bot fail every call. Operator decision: accept "no OS layer."

The server-side `settings.json` SHALL set `sandbox.enabled: false` explicitly — there SHALL NOT be an ambiguous "enabled but silently absent" state. With no OS layer, the credential files, environment, and network SHALL be protected at the tool layer by the `permissions.deny` family, and the resulting residual risk (name-based deny is evadable by a determined shell) SHALL be documented in `SECURITY.md`.

#### Scenario: Sandbox is explicitly off, not silently degraded
- **WHEN** `settings.json` is inspected
- **THEN** `sandbox.enabled` is `false` (an explicit, reviewable state), not absent or implicitly disabled

#### Scenario: Residual risk of the absent OS layer is disclosed
- **WHEN** an operator reads `SECURITY.md`
- **THEN** it states there is no OS sandbox and that tool-layer deny is name-based and evadable, so execution tiers must be limited to trusted users

### Requirement: Full bypass is an opt-in tier, default closed
Full `bypassPermissions` execution SHALL be reachable only when an operator explicitly opts in, and SHALL be off by default. The default channel mode SHALL remain a safe read/plan mode.

#### Scenario: Bypass requires explicit opt-in
- **WHEN** no operator opt-in for full bypass is configured
- **THEN** no invocation can run in `bypassPermissions`

#### Scenario: Default mode is safe
- **WHEN** a channel has no stored mode
- **THEN** its effective mode is the read/plan default

### Requirement: Optional per-command human approval tier
The execution path SHALL support an optional `--permission-prompt-tool` MCP approver that routes each dangerous command to a human for per-command approval, replacing whole-plan approval. When enabled, a command SHALL execute only after an explicit approval response.

#### Scenario: Command awaits per-command approval
- **WHEN** the approver tier is enabled and the agent requests a dangerous command
- **THEN** the command executes only after a human returns an allow decision, and is skipped on a deny decision

#### Scenario: Approver tier is optional
- **WHEN** the approver tier is not enabled
- **THEN** the execution path still enforces the allow-list, deny rules, and sandbox
</content>
