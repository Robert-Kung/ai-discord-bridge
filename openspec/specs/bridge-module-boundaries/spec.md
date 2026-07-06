# bridge-module-boundaries Specification

## Purpose
TBD - created by archiving change bridge-restructure. Update Purpose after archive.
## Requirements
### Requirement: Business logic lives in service modules, not the Discord frontend
The bridge SHALL be organized as a package whose modules separate concerns along the trust seams: configuration, execution runner, memory, trust/tier logic, approver IPC, discussion, and Discord frontend. The Discord frontend SHALL be a thin I/O wrapper and SHALL NOT contain memory, permission, or execution business logic.

#### Scenario: Frontend is thin I/O
- **WHEN** the frontend module is inspected
- **THEN** it handles Discord events and delegates memory/permission/execution work to service modules, holding no such logic itself

#### Scenario: Behavior is preserved by the split
- **WHEN** the existing test suite runs against the restructured package
- **THEN** all security-critical assertions (fail-closed auth, path/escape guard, trust filtering, env scrub, layer split) pass unchanged

### Requirement: The execution chokepoint is importable in isolation
The execution runner SHALL be importable without importing the Discord frontend or the memory module, so it can be hosted in a separate executor context. A package-wide import allowlist SHALL be mechanically enforced: only the frontend imports `execute`; only the runner contains the subprocess launch and the `claude -p` argv assembly; conversation-layer modules (discussion, memory) import `converse` at most; no module outside the runner references the private chokepoint (`_call_claude`) or the argv builder.

#### Scenario: Runner has no Discord or memory dependency
- **WHEN** the runner module is imported
- **THEN** it does not require the Discord frontend module nor the memory module

#### Scenario: No module bypasses the layer entries
- **WHEN** the import-boundary check scans the whole package
- **THEN** only the frontend imports `execute`, only the runner launches subprocesses or assembles the `claude -p` argv, and conversation-layer modules reference `converse` at most

### Requirement: Reloadable configuration is never captured by value
Configuration values populated by `load_config` SHALL be accessed as module attributes, never captured via from-imports, so a reload is visible to every consumer. A mechanical check SHALL enforce this package-wide.

#### Scenario: Stale-capture is mechanically rejected
- **WHEN** a module from-imports a reloadable configuration name (or a shared mutable state member)
- **THEN** the boundary check fails, preventing the silently-dead-consumer failure mode (fail-closed behavior with a green canary)

