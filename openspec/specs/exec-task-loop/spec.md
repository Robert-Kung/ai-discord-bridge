# exec-task-loop

## Purpose
Contain execution-tier edit outcomes: every task runs in a throwaway git worktree, reaches the live checkout only through a human diff-review merge gate, is optionally verified by a bridge-owned per-project command, and may receive message attachments as sandboxed untrusted context.

## Requirements

### Requirement: Execution-tier tasks run on an isolated git worktree
An execution-tier task SHALL run in a dedicated git worktree branched from the target project's HEAD, never in the live checkout. The live working tree SHALL NOT be modified by a running job.

#### Scenario: Job does not touch the live checkout
- **WHEN** an execution-tier task edits files
- **THEN** the edits occur in a worktree on a `bridge/<job-id>` branch, and the operator's live checkout is unchanged until merge

#### Scenario: Discarded job leaves no trace
- **WHEN** a job is rejected or cancelled
- **THEN** its worktree is removed and its branch deleted, and the live checkout is unaffected

### Requirement: Edits merge only after human diff review
On task completion the bridge SHALL commit the job's changes to its `bridge/<job-id>` branch (the branch, not the worktree, is the recoverable source of truth), post the diff for review (`--stat` inline plus the full diff as a file), and merge into the original branch only on an explicit human approval. Rejection discards the branch; a lapsed review window parks the job as awaiting-review — completed work SHALL never be auto-deleted on timeout.

#### Scenario: Approval merges the branch
- **WHEN** a whitelisted user approves the posted diff and the live checkout is clean
- **THEN** the `bridge/<job-id>` branch is merged into the original branch

#### Scenario: Dirty live checkout refuses the merge
- **WHEN** a diff is approved but the live checkout has uncommitted changes
- **THEN** the bridge refuses the merge and reports the branch name with manual merge instructions

#### Scenario: Rejection discards the branch
- **WHEN** the review is rejected
- **THEN** no merge occurs and the worktree/branch are removed

#### Scenario: Review timeout parks, never deletes
- **WHEN** the reaction window elapses on a completed job
- **THEN** the job becomes awaiting-review, its branch and persisted diff survive (including across restarts), and explicit merge/discard commands act on it later

#### Scenario: Merge conflict is aborted and reported, never forced
- **WHEN** the original branch has advanced and the merge conflicts
- **THEN** the bridge aborts the merge (leaving no conflict markers in the live tree), reports the conflict with recovery commands, and keeps the branch for manual merge

### Requirement: Post-task verification runs the project's own test command
When a project configures a verify command, the bridge SHALL run it inside the job worktree after the agent finishes and SHALL report pass/fail with output; when none is configured it SHALL state that explicitly and never imply success. The verify command SHALL be sourced from bridge-owned configuration outside the repository (never from a file inside the worktree, which the agent can author), SHALL run with a stripped environment (no Discord tokens or API keys) and its own timeout, and SHALL be enabled only under the same containment gate as exec-tier Bash (verification is agent-influenced execution).

#### Scenario: Configured verify runs and is reported
- **WHEN** a project has a verify command configured outside the repo and a job completes with the containment gate proven
- **THEN** the command runs in the worktree with a stripped env and its pass/fail + output tail is appended to the result

#### Scenario: Verify command cannot be authored by the agent
- **WHEN** the job's diff adds or modifies any in-repo file purporting to configure verification
- **THEN** the bridge ignores it — the verify command is read only from bridge-owned configuration outside the repository

#### Scenario: No verify configured is stated, not faked
- **WHEN** a project has no verify command
- **THEN** the result says verification was not configured and makes no green claim

### Requirement: Message attachments become job context
Attachments on a whitelisted user's triggering message SHALL be downloaded — sanitized to a safe basename, subject to size and count caps — into a job directory outside the repository worktree, and their paths supplied to the agent as clearly-delimited untrusted content.

#### Scenario: Attachment is available to the agent
- **WHEN** a whitelisted user attaches a file to an execution-tier request
- **THEN** the file is downloaded into the job's attachment directory (outside the worktree, so it can never shadow repo files or enter the diff) and its path is included in the prompt as untrusted context

#### Scenario: Attachment limits are enforced
- **WHEN** an attachment exceeds the size cap, exceeds the per-message count cap, or carries a path-traversal filename
- **THEN** it is rejected (oversize/overcount) or stored under a sanitized basename, and the job proceeds without it where rejected
