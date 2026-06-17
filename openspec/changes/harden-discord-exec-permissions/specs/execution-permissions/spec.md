## ADDED Requirements

### Requirement: Allow-listed command execution replaces full bypass
The execution path SHALL run with `--permission-mode acceptEdits` plus an explicit `--allowedTools` command allow-list. Commands not on the allow-list SHALL NOT execute. Full `bypassPermissions` SHALL NOT be the default execution mode.

#### Scenario: Allow-listed command runs
- **WHEN** the agent invokes a command present on the allow-list (e.g. a test or git command)
- **THEN** the command executes without a permission prompt

#### Scenario: Non-allow-listed command is blocked
- **WHEN** the agent attempts a command not on the allow-list
- **THEN** the command does not execute

#### Scenario: Compound command is not auto-approved by a single rule
- **WHEN** the agent submits a compound command joining an allow-listed command with a non-allow-listed one (via `&&`, `|`, `;`, etc.)
- **THEN** the non-allow-listed segment does not execute

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

### Requirement: OS sandbox isolates execution
The execution call SHALL enable the Claude Code OS sandbox configured to deny reads of credential files and restrict network. The sandbox SHALL be configured to fail closed: it SHALL NOT silently degrade to unsandboxed execution if it cannot start, and commands SHALL NOT escape the sandbox on failure.

#### Scenario: Sandbox blocks credential read at OS level
- **WHEN** the agent attempts to read a credential file path listed in the sandbox deny-read set
- **THEN** the OS sandbox blocks the read independently of the tool-level deny rules

#### Scenario: Sandbox unavailable fails closed
- **WHEN** the sandbox cannot be initialized
- **THEN** the execution call fails rather than running unsandboxed

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
