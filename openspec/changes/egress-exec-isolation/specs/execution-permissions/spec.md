## MODIFIED Requirements

### Requirement: Enforced deny rules survive any mode
A server-side `settings.json` SHALL be passed via `--settings` on every execution call, containing a `permissions.deny` set that blocks credential reads (`~/.claude` config dirs and the apiKeyHelper key file), environment dumping (`env`, `printenv`), and client-side network fetch (`curl`, `wget`, `WebFetch`). These deny rules SHALL be enforced even when an invocation runs in bypass mode. With `egress-containment` in force, the deny family's role is defense-in-depth: the primary exfiltration barrier is the network-layer allow-list, not command-name matching. `WebSearch` SHALL NOT be denied — it executes server-side at Anthropic and adds no container egress; denying it removes research capability without reducing the egress surface.

#### Scenario: Credential read is denied
- **WHEN** the agent attempts to read a mounted credential file or the apiKeyHelper key file via any tool or shell command
- **THEN** the attempt is denied

#### Scenario: Environment dump is denied
- **WHEN** the agent runs `printenv` or `env`
- **THEN** the command is denied

#### Scenario: Deny holds under bypass
- **WHEN** an invocation runs in the opt-in full-bypass tier
- **THEN** the deny rules still block credential reads, env dumps, and client-side network fetch

#### Scenario: WebSearch is available to the agent
- **WHEN** the agent invokes `WebSearch` under any permission mode with egress containment proven
- **THEN** the tool is not blocked by the deny family
