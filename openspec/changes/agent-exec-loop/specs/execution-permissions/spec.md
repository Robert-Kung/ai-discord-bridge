## MODIFIED Requirements

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
