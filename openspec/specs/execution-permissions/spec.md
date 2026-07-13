# execution-permissions

## Purpose
Define the permission model for the human-driven execution path — acceptEdits contained by an enforced deny family plus the worktree diff-review gate, network-layer egress as the primary exfiltration barrier once proven, a phase-2-gated exec-tier Bash relaxation, an explicitly-disabled OS sandbox with documented residual, a default-closed full-bypass tier, and an optional per-command human-approval tier.

## Requirements

### Requirement: Execution mode is acceptEdits contained by the deny family (NOT an allow-list)
The execution path SHALL run with `--permission-mode acceptEdits` and SHALL NOT pass an `--allowedTools` execution list (it does not restrict). Containment of what executes SHALL come from the enforced `permissions.deny` family and, once `egress-containment` is proven in force, the network-layer egress allow-list. Full `bypassPermissions` SHALL NOT be the default execution mode. A restrictive per-command allow-list SHALL be provided only by the optional approver tier.

Execution-tier edits SHALL be contained by the git worktree + diff-review merge gate (see `exec-task-loop`): the agent may edit and run commands within a throwaway worktree, and nothing reaches the live checkout without human diff approval. This post-hoc review is the primary containment for edit *outcomes*, superseding whole-plan pre-approval for coding tasks.

The execution tier MAY run with Bash permitted ONLY inside the phase-2 executor container of `egress-containment` (Anthropic-only egress, mounts limited to the job worktree plus the credential), proven at startup by that container's own fail-closed egress canary. A green single-container (phase-1) canary SHALL NOT enable the relaxation: in the shared container the diff gate cannot observe writes to the other rw mounts, and phase-1 egress still allows Discord endpoints. Post-task verification (see `exec-task-loop`) is agent-influenced execution and SHALL be enabled only under this same gate. The credential-read / env-dump denies hold regardless.

#### Scenario: Execution runs under acceptEdits without an allow-list flag
- **WHEN** the human-driven execution path invokes the agent
- **THEN** the invocation uses `--permission-mode acceptEdits` and emits no `--allowedTools` flag

#### Scenario: A denied command does not execute under the execution mode
- **WHEN** the agent attempts a command covered by the `permissions.deny` family (credential read, `env`/`printenv`) under `acceptEdits`
- **THEN** the command is denied and does not execute

#### Scenario: Edit outcomes are gated by diff review
- **WHEN** an execution-tier task produces file edits
- **THEN** they are confined to the job worktree and reach the live checkout only after human diff approval

#### Scenario: Bash relaxation requires the phase-2 executor posture
- **WHEN** the bridge starts with the exec-tier Bash relaxation enabled but the phase-2 executor posture (split container, worktree-only mounts, executor-local egress canary) is not proven
- **THEN** the relaxation does not take effect (fail-closed to the pre-relaxation posture), even if a single-container egress canary is green

#### Scenario: Restrictive command confinement is the approver tier's job
- **WHEN** true "only these commands may run" confinement is required for a non-git one-shot command
- **THEN** it is provided by the optional `--permission-prompt-tool` approver, not by `--allowedTools`

### Requirement: Enforced deny rules survive any mode
A server-side `settings.json` SHALL be passed via `--settings` on every execution call, containing a `permissions.deny` set that blocks credential reads (`~/.claude` config dirs and the apiKeyHelper key file), environment dumping (`env`, `printenv`), and client-side network fetch (`curl`, `wget`, `WebFetch`). These deny rules SHALL be enforced even when an invocation runs in bypass mode. With `egress-containment` in force, the deny family's role is defense-in-depth: the primary exfiltration barrier is the network-layer allow-list, not command-name matching. No deny entries are removed by this change.

#### Scenario: Credential read is denied
- **WHEN** the agent attempts to read a mounted credential file or the apiKeyHelper key file via any tool or shell command
- **THEN** the attempt is denied

#### Scenario: Environment dump is denied
- **WHEN** the agent runs `printenv` or `env`
- **THEN** the command is denied

#### Scenario: Deny holds under bypass
- **WHEN** an invocation runs in the opt-in full-bypass tier
- **THEN** the deny rules still block credential reads, env dumps, and client-side network fetch

#### Scenario: Deny list contents are unchanged by containment
- **WHEN** egress containment is proven in force
- **THEN** the settings canary still proves the same credential/env/WebFetch deny family loaded (network containment adds a layer; it removes nothing)

### Requirement: OS sandbox is explicitly disabled, residual documented (no silent degrade)
The server-side `settings.json` SHALL set `sandbox.enabled: false` explicitly — there SHALL NOT be an ambiguous "enabled but silently absent" state. With no OS layer, the credential files, environment, and network SHALL be protected at the tool layer by the `permissions.deny` family, and the resulting residual risk (name-based deny is evadable by a determined shell) SHALL be documented in `SECURITY.md`.

> Revised after preflight gate 0.2 (see `preflight-findings.md`): Claude Code's bubblewrap
> sandbox cannot start in the container (bubblewrap absent + unprivileged user namespaces
> blocked by Docker's default seccomp/caps), so enabling it with `failIfUnavailable: true`
> would make the bot fail every call. Operator decision: accept "no OS layer."

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
