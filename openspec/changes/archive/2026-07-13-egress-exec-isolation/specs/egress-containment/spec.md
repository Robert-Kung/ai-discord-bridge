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

### Requirement: Egress containment is proven at startup by three probes, fail-closed
The bridge SHALL run an egress canary before serving, with three probes: (1) a **direct** connection attempt to a non-allow-listed control host MUST fail (the route is gone); (2) the same control host attempted **via the proxy** MUST be denied by the proxy, not tunneled (the allow-list is actually default-deny); (3) the Anthropic endpoint via the proxy MUST be reachable. If probe 1 or probe 2 fails its expectation, the bridge SHALL refuse to serve (misconfigured network posture is a hard failure, not a retry case). Probe-3 failure is transient and retries with backoff.

#### Scenario: Open egress refuses to serve
- **WHEN** the startup canary finds the non-allow-listed control host directly reachable
- **THEN** the bridge exits without serving any Discord traffic

#### Scenario: Allow-all proxy refuses to serve
- **WHEN** the direct route is gone but the proxy tunnels a connection to the non-allow-listed control host (misconfigured/empty ACL)
- **THEN** the bridge exits without serving — a connectivity-only canary that passes this state is explicitly non-conforming

#### Scenario: Unreachable Anthropic endpoint waits, not crash-loops
- **WHEN** the canary finds the Anthropic endpoint unreachable via the proxy (proxy down / transient)
- **THEN** the bridge retries with backoff in-process instead of exiting

### Requirement: Split containers give each secret a single-purpose egress (phase 2)
After the frontend/executor split, the frontend container (Discord tokens, no Claude credentials) SHALL have Discord-only egress, and the executor container (Claude credentials, no Discord tokens) SHALL have Anthropic-only egress. Each container SHALL run its own fail-closed egress canary asserting its own deny direction — in particular the executor (where the credential lives) proves Discord unreachability from inside itself; a frontend-side canary proves nothing about the executor.

#### Scenario: Executor cannot reach Discord
- **WHEN** a process in the executor container attempts to reach a Discord endpoint
- **THEN** the connection fails at the network layer, and the executor's own startup canary proves this before the executor serves

#### Scenario: Frontend cannot reach Anthropic
- **WHEN** a process in the frontend container attempts to reach the Anthropic API
- **THEN** the connection fails at the network layer, and the frontend's own startup canary proves this before the frontend serves
