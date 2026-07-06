## ADDED Requirements

### Requirement: Business logic lives in service modules, not the Discord frontend
The bridge SHALL be organized as a package whose modules separate concerns along the trust seams: configuration, execution runner, memory, trust/tier logic, approver IPC, discussion, and Discord frontend. The Discord frontend SHALL be a thin I/O wrapper and SHALL NOT contain memory, permission, or execution business logic.

#### Scenario: Frontend is thin I/O
- **WHEN** the frontend module is inspected
- **THEN** it handles Discord events and delegates memory/permission/execution work to service modules, holding no such logic itself

#### Scenario: Behavior is preserved by the split
- **WHEN** the existing test suite runs against the restructured package
- **THEN** all security-critical assertions (fail-closed auth, path/escape guard, trust filtering, env scrub, layer split) pass unchanged

### Requirement: The execution chokepoint is importable in isolation
The execution runner SHALL be importable without importing the Discord frontend, so it can be hosted in a separate executor context. The frontend SHALL reach execution only through the public layer entries (`converse`/`execute`) and SHALL NOT reference the private chokepoint (`_call_claude`).

#### Scenario: Runner has no Discord dependency
- **WHEN** the runner module is imported
- **THEN** it does not require the Discord frontend module

#### Scenario: Frontend cannot bypass the layer entries
- **WHEN** an import-boundary check scans the frontend module
- **THEN** it finds no reference to the private chokepoint, only the public `converse`/`execute` entries
