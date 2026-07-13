# exec-jobs-observability

## Purpose
Make execution-tier work observable and controllable: tracked cancellable background jobs, live progress streaming, and an independent execution timeout that never orphans subprocesses.

## Requirements

### Requirement: Execution-tier tasks run as tracked, cancellable background jobs
An execution-tier task SHALL run as a background job with a stable id, recorded in a registry (in memory plus an on-disk mirror). A running job SHALL NOT block conversation-layer calls. The operator SHALL be able to list jobs and cancel a running one.

#### Scenario: Long job does not stall conversation
- **WHEN** an execution job is running and a whitelisted user sends an `@`-mention conversation message
- **THEN** the conversation call proceeds without waiting for the job to finish

#### Scenario: List jobs
- **WHEN** a whitelisted user issues the list-jobs command
- **THEN** the bridge reports each job's id, status, target project, and age

#### Scenario: Cancel terminates the process group and cleans up
- **WHEN** a whitelisted user cancels a running job
- **THEN** the job's process group is terminated and its worktree is removed

### Requirement: Execution jobs stream live progress
An execution job SHALL surface incremental progress by maintaining a single status message updated (rate-limited) with a compact trace of the agent's actions, rather than emitting nothing until completion.

#### Scenario: Progress is visible during a job
- **WHEN** an execution job performs tool actions over time
- **THEN** its status message is updated with a recent-actions trace before the final result

### Requirement: Execution timeout kills the process group and preserves state
Execution jobs SHALL use a dedicated, larger timeout than conversation calls. On timeout the whole process group SHALL be killed (no orphaned children) and the job's partial worktree state SHALL be reported, not silently discarded.

#### Scenario: Exec timeout is independent of conversation timeout
- **WHEN** an execution job exceeds the execution timeout
- **THEN** it is terminated on the execution timeout, independent of the shorter conversation timeout

#### Scenario: Timeout does not orphan subprocesses
- **WHEN** an execution job is killed on timeout
- **THEN** its child processes are terminated with it and the partial diff is available for inspection
