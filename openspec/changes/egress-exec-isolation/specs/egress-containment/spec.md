## ADDED Requirements

### Requirement: Execution-path egress is allow-listed at the network layer
The container running `claude -p` SHALL have no direct network route out; all egress SHALL pass through a default-deny hostname allow-list proxy. The allow-list SHALL contain only the Anthropic API endpoints (plus, while frontend and executor share one container, the Discord endpoints the bridge process needs). Name-based tool denies (`curl`/`wget`/`WebFetch`) remain as defense-in-depth but SHALL NOT be the primary exfiltration barrier.

#### Scenario: Non-allow-listed host is unreachable
- **WHEN** any process in the execution container attempts a connection to a host not on the allow-list (directly or via the proxy)
- **THEN** the connection fails at the network layer, regardless of which tool or shell trick issued it

#### Scenario: Anthropic API remains reachable
- **WHEN** a `claude -p` subprocess calls the Anthropic API via the configured proxy
- **THEN** the call succeeds

#### Scenario: DNS cannot be used as a side channel
- **WHEN** a process on the internal network attempts direct DNS resolution to an external resolver
- **THEN** the attempt fails; only the proxy resolves hostnames

### Requirement: Egress containment is proven at startup, fail-closed
The bridge SHALL run an egress canary before serving: a connection attempt to a non-allow-listed control host MUST fail, and the Anthropic endpoint MUST be reachable. If the control host is reachable, the bridge SHALL refuse to serve (misconfigured network posture is a hard failure, not a retry case).

#### Scenario: Open egress refuses to serve
- **WHEN** the startup canary finds the non-allow-listed control host reachable
- **THEN** the bridge exits without serving any Discord traffic

#### Scenario: Unreachable Anthropic endpoint waits, not crash-loops
- **WHEN** the canary finds the Anthropic endpoint unreachable (proxy down / transient)
- **THEN** the bridge retries with backoff in-process instead of exiting

### Requirement: Split containers give each secret a single-purpose egress (phase 2)
After the frontend/executor split, the frontend container (Discord tokens, no Claude credentials) SHALL have Discord-only egress, and the executor container (Claude credentials, no Discord tokens) SHALL have Anthropic-only egress.

#### Scenario: Executor cannot reach Discord
- **WHEN** a process in the executor container attempts to reach a Discord endpoint
- **THEN** the connection fails at the network layer

#### Scenario: Frontend cannot reach Anthropic
- **WHEN** a process in the frontend container attempts to reach the Anthropic API
- **THEN** the connection fails at the network layer
