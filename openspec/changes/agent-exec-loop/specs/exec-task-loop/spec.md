## ADDED Requirements

### Requirement: Execution-tier tasks run on an isolated git worktree
An execution-tier task SHALL run in a dedicated git worktree branched from the target project's HEAD, never in the live checkout. The live working tree SHALL NOT be modified by a running job.

#### Scenario: Job does not touch the live checkout
- **WHEN** an execution-tier task edits files
- **THEN** the edits occur in a worktree on a `bridge/<job-id>` branch, and the operator's live checkout is unchanged until merge

#### Scenario: Discarded job leaves no trace
- **WHEN** a job is rejected or cancelled
- **THEN** its worktree is removed and its branch deleted, and the live checkout is unaffected

### Requirement: Edits merge only after human diff review
On task completion the bridge SHALL post the job's diff (as a file attachment) for review; the branch SHALL merge into the original branch only on an explicit human approval, and SHALL be discarded on rejection or timeout.

#### Scenario: Approval merges the branch
- **WHEN** a whitelisted user approves the posted diff
- **THEN** the `bridge/<job-id>` branch is merged into the original branch

#### Scenario: Rejection or timeout discards the branch
- **WHEN** the review is rejected or the reaction window elapses
- **THEN** no merge occurs and the worktree/branch are removed

#### Scenario: Merge conflict is reported, never forced
- **WHEN** the original branch has advanced and the merge conflicts
- **THEN** the bridge reports the conflict and leaves the branch for manual merge, without force-merging

### Requirement: Post-task verification runs the project's own test command
When a project configures a verify command, the bridge SHALL run it inside the job worktree after the agent finishes and SHALL report pass/fail with output; when none is configured it SHALL state that explicitly and never imply success.

#### Scenario: Configured verify runs and is reported
- **WHEN** a project has a verify command and a job completes
- **THEN** the command runs in the worktree and its pass/fail + output tail is appended to the result

#### Scenario: No verify configured is stated, not faked
- **WHEN** a project has no verify command
- **THEN** the result says verification was not configured and makes no green claim

### Requirement: Message attachments become job context
Attachments on a whitelisted user's triggering message SHALL be downloaded into the job workspace and their paths supplied to the agent as clearly-delimited untrusted content.

#### Scenario: Attachment is available to the agent
- **WHEN** a whitelisted user attaches a file to an execution-tier request
- **THEN** the file is downloaded into the job workspace and its path is included in the prompt as untrusted context
