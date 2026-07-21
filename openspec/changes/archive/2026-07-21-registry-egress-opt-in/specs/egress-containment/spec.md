# egress-containment (delta)

## MODIFIED Requirements

### Requirement: Execution-path egress is allow-listed at the network layer
The container running `claude -p` SHALL have no direct network route out; all egress SHALL pass through a default-deny hostname allow-list proxy. The allow-list SHALL contain only the Anthropic API endpoints and a pinned set of read-only documentation hosts (plus, while frontend and executor share one container, the Discord endpoints the bridge process needs). It MAY additionally contain **read-only** package-index hosts when the operator opts in at proxy build time. No host that also serves a publish/upload endpoint SHALL be allow-listed, opt-in or otherwise: because the proxy is CONNECT-only with no TLS bump, "reachable" means "arbitrary method and body", so a publish-capable host is a write path out. Opt-in hosts SHALL live in a separate filter file appended to the base allow-list — never merged into it — and SHALL default to off. Name-based tool denies (`curl`/`wget`/`WebFetch`) remain as defense-in-depth but SHALL NOT be the primary exfiltration barrier.

#### Scenario: Non-allow-listed host is unreachable
- **WHEN** any process in the execution container attempts a connection to a host not on the allow-list (directly or via the proxy)
- **THEN** the connection fails at the network layer, regardless of which tool or shell trick issued it

#### Scenario: Anthropic API remains reachable
- **WHEN** a `claude -p` subprocess calls the Anthropic API via the configured proxy
- **THEN** the call succeeds

#### Scenario: DNS cannot be used as a side channel
- **WHEN** a process on the internal network attempts direct DNS resolution to an external resolver
- **THEN** the attempt fails; only the proxy resolves hostnames

#### Scenario: Package-index hosts are unreachable by default
- **WHEN** the executor proxy is built without the opt-in and a process attempts to reach a package index
- **THEN** the proxy denies the CONNECT and the allow-list file contains no package-index entries

#### Scenario: Opt-in adds read-only index hosts without widening anything else
- **WHEN** the executor proxy is built with the package-index filter appended
- **THEN** the read-only index hosts become reachable, every previously allow-listed host stays reachable, and every other host stays denied

#### Scenario: Publish-capable hosts stay denied under opt-in
- **WHEN** the opt-in is enabled and a process attempts to reach a package-upload endpoint (`upload.pypi.org`) or a registry whose host also accepts publishes (`registry.npmjs.org`)
- **THEN** the proxy denies the CONNECT, and no filter file shipped in the repo contains those hosts

#### Scenario: Opt-in does not apply to the frontend proxy
- **WHEN** the frontend-side proxy is built
- **THEN** its allow-list contains Discord hosts only, and a build combining the frontend filter with the package-index filter fails at image build time rather than producing a widened proxy

#### Scenario: Opt-in does not apply to single-container deployments
- **WHEN** the deployment runs the single-container posture (combined filter, one container holding both Discord tokens and Claude credentials)
- **THEN** the package-index opt-in is not supported, because the credential-versus-egress pairing that bounds the residual risk does not hold there

### Requirement: Egress containment is proven at startup by three probes, fail-closed
The bridge SHALL run an egress canary before serving, with three probes: (1) a **direct** connection attempt to a non-allow-listed control host MUST fail (the route is gone); (2) the same control host attempted **via the proxy** MUST be denied by the proxy, not tunneled (the allow-list is actually default-deny); (3) the Anthropic endpoint via the proxy MUST be reachable. If probe 1 or probe 2 fails its expectation, the bridge SHALL refuse to serve (misconfigured network posture is a hard failure, not a retry case). Probe-3 failure is transient and retries with backoff. Each container's canary SHALL treat the hosts it must never reach as forbidden probes — for the frontend this includes the package-index hosts, so that a build-time misconfiguration granting the Discord-token container index egress is caught fail-closed rather than passing silently. Package-index reachability SHALL NOT be a startup gate on the executor side: an index outage must not take the executor down.

#### Scenario: Open egress refuses to serve
- **WHEN** the startup canary finds the non-allow-listed control host directly reachable
- **THEN** the bridge exits without serving any Discord traffic

#### Scenario: Allow-all proxy refuses to serve
- **WHEN** the direct route is gone but the proxy tunnels a connection to the non-allow-listed control host (misconfigured/empty ACL)
- **THEN** the bridge exits without serving — a connectivity-only canary that passes this state is explicitly non-conforming

#### Scenario: Unreachable Anthropic endpoint waits, not crash-loops
- **WHEN** the canary finds the Anthropic endpoint unreachable via the proxy (proxy down / transient)
- **THEN** the bridge retries with backoff in-process instead of exiting

#### Scenario: Default-deny proof still required under opt-in
- **WHEN** the executor starts with the package-index opt-in enabled
- **THEN** the canary still asserts probes 1 and 2 against the control host and still refuses to serve if either fails

#### Scenario: Frontend with index egress refuses to serve
- **WHEN** the frontend container starts and finds a package-index host reachable
- **THEN** the frontend exits without serving, because the Discord-token container must never hold index egress

#### Scenario: Index outage does not block startup
- **WHEN** the opt-in is enabled but a package-index host is unreachable
- **THEN** the executor serves normally and the failure surfaces only when an install is attempted
