# agent-trust-layers

## Purpose
Separate the bot↔bot conversation layer (no execution capability) from the human-driven execution layer, with a single permission/exec chokepoint and a structural guarantee that the conversation layer cannot emit write/execute permissions.

## Requirements

### Requirement: Single execution chokepoint
All construction of `claude -p` invocation arguments (permission mode, allowed tools, settings path) SHALL occur in exactly one function. No other code path SHALL assemble or launch a `claude -p` subprocess.

#### Scenario: Only the chokepoint builds exec args
- **WHEN** the codebase is searched for `claude -p` subprocess construction
- **THEN** exactly one function assembles the argument list and launches the subprocess

#### Scenario: Permission inputs flow only through the chokepoint
- **WHEN** any layer needs to run the agent
- **THEN** it calls the chokepoint with declared inputs (mode, cwd, prompt) and cannot bypass it to set permission flags directly

### Requirement: Conversation layer has no execution capability
The bot↔bot conversation layer (A↔B debate, summaries, memory) SHALL only ever cause read/plan-mode invocations. It SHALL NOT be able to emit `acceptEdits` or `bypassPermissions`, nor pass an `--allowedTools` execution list.

#### Scenario: Conversation layer emits plan mode only
- **WHEN** the conversation layer triggers an agent call
- **THEN** the resulting permission mode is the read/plan mode and no write/execute permission is present

#### Scenario: Conversation layer cannot escalate
- **WHEN** a conversation-layer code path is exercised with any input
- **THEN** it is structurally unable to produce a write- or execute-capable invocation (verified by test over the conversation-layer entry points)

### Requirement: Conversation-layer output is persisted by the harness, not the subprocess
Because the conversation layer runs in read/plan mode and cannot write files via the agent subprocess, any plan/decision output it produces SHALL be persisted by the harness (`bot.py`) — to a shared `discord-*` dir — independently of the subprocess permission mode. The conversation layer SHALL NOT rely on the plan-mode subprocess writing files. A plan document that the agent itself authors SHALL require the execution layer's `edit` tier.

#### Scenario: Plan persistence does not depend on subprocess write capability
- **WHEN** the conversation layer produces a plan/decision to persist
- **THEN** the harness writes it (mode-independent), and no plan-mode subprocess file write is required

#### Scenario: Agent-authored plan document requires edit tier
- **WHEN** the agent itself must write a full plan document to disk
- **THEN** the invocation is on the execution layer's `edit` tier, not the default plan mode

#### Scenario: The operator index cannot be clobbered by the execution path
- **WHEN** the execution path is considered against the `project_plan.md` summary index
- **THEN** that file is mounted read-only into the container, so the execution path cannot overwrite it at all (a stronger guarantee than an in-process helper); maintaining the index is an operator-side concern

#### Scenario: Harness state files rotate a snapshot on write
- **WHEN** the harness writes the channel summary or project notes it persists
- **THEN** the prior version is rotated to a timestamped snapshot, so a flush never silently clobbers accumulated state

### Requirement: Inter-agent discussion uses Discord mention, not the operator CLI
The bot↔bot conversation layer SHALL exchange messages over Discord `@`-mention. It SHALL NOT invoke the operator's `sibling` CLI, which is unavailable headless / inside the container and is a CLI-only operator tool.

#### Scenario: Bots discuss via mention
- **WHEN** one bot addresses the other during a discussion
- **THEN** it does so via a Discord `@`-mention message, not a `sibling` invocation

#### Scenario: sibling is never shelled from the bot
- **WHEN** the bot code paths are searched for inter-agent communication
- **THEN** no path invokes `sibling`

### Requirement: Execution layer is the only execution surface
The human-driven execution layer SHALL be the only layer permitted to request write or execute capability, and only after the authorization checks for the requesting user and channel mode pass.

#### Scenario: Execution requires the execution layer
- **WHEN** a write- or execute-capable invocation is produced
- **THEN** it originated from the execution layer with a passing authorization check

#### Scenario: Unauthorized execution request is refused
- **WHEN** a non-whitelisted user attempts to trigger an execute-capable invocation
- **THEN** the request is refused before any subprocess is launched

### Requirement: Auto tier is an opt-in execution posture above bypass
The `auto` channel mode SHALL be an execution-layer tier that carries MORE authority than `bypass` (it resolves the merge decision without a human), and SHALL therefore be gated by BOTH the `ENABLE_AUTO_MERGE` environment flag AND author membership in `ALLOWED_USER_IDS` — the same flag-plus-whitelist posture as bypass/approve, never the flag alone. The conversation layer SHALL never reach the auto tier (a bot-origin mention cannot drive execution, unchanged). Enabling auto SHALL additionally require the verify path and the cross-account evaluator to be available; absent either, `!mode auto` SHALL be refused (fail-closed).

#### Scenario: auto requires flag and whitelist
- **WHEN** a user issues `!mode auto`
- **THEN** it is honoured only if `ENABLE_AUTO_MERGE` is set AND the user is in `ALLOWED_USER_IDS`, and is refused otherwise

#### Scenario: auto is re-validated per message
- **WHEN** a message dispatches under channel mode `auto`
- **THEN** the tier is re-checked at dispatch (the mode is in `VALID_MODES` and the dispatch-time downgrade list), so a revoked flag/whitelist takes effect immediately

#### Scenario: conversation layer cannot reach auto
- **WHEN** a bot-origin mention arrives on an auto-mode channel
- **THEN** it is handled by the conversation layer (plan only) and never resolves a merge

### Requirement: The auto tier's merge signal is not claimed unforgeable
The auto tier replaces the human merge decision with two signals — verify (agent-authored tests) and the cross-account evaluator (the OTHER bot, same model family, reviewing the same diff). The security posture SHALL document that these signals are CORRELATED, not independent, and that a diff-content prompt injection may steer the evaluator into genuinely emitting `VERDICT: approve`. "Two green signals" SHALL be documented to mean "not obviously bad", never "safe"; the auto tier SHALL NOT be presented as sealing the verdict channel against injection.

#### Scenario: security posture states the residual
- **WHEN** SECURITY.md describes the auto tier
- **THEN** it names the evaluator-injection residual and the verify/evaluator correlation, rather than asserting the verdict channel is unforgeable
