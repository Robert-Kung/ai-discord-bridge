## Context

Credential-exfiltration containment is currently name-based (`permissions.deny` on `curl`/`wget`/`env`/`WebFetch` + credential-path Read denies) and acknowledged evadable (SECURITY.md §6). The container has unrestricted egress (SECURITY.md §7). Two processes with different network needs share it: the bridge (needs Discord gateway/REST/CDN) and `claude -p` subprocesses (need Anthropic API only). `docker-compose.yml` and `docs/` are gitignored — enforcement must therefore be mirrored in `docker-compose.example.yml` and tracked docs to be reviewable in-diff.

**Threat-model clarity (D2):** egress containment addresses the *autonomous/injection-driven* exfil adversary. It does nothing against a trusted bypass-mode user having the agent print secrets into its own reply (the reply channel is application output, not a network syscall). That residual is accepted and documented; the design must not claim otherwise.

## Goals / Non-Goals

**Goals:**
- Phase 2: an agent that reads a credential has no network path anywhere except Anthropic. Phase 1: network plumbing + canary land; surface collapses to "Anthropic or Discord" (scaffold, not containment — Discord webhooks remain a sink).
- Containment is *proven* at startup by a three-probe fail-closed canary, including proxy default-deny.
- API-key mode keys leave the subprocess env (`apiKeyHelper`) — independent hardening of the lesser secret.
- OAuth-refresh reachability proven before rollout, with proxy deny-logging for anything blocked afterward.

**Non-Goals:**
- OS sandboxing inside the container (bubblewrap remains unavailable; unchanged decision from gate 0.2).
- Per-process egress split inside a single container (phase 2 does it with container boundaries).
- Removing deny-list entries (`WebSearch` was never denied; nothing to give back).
- Disposable-account rotation; reply-channel exfil residual.

## Decisions

- **Step 0 — blocking spike: discord.py gateway over CONNECT proxy.** Before any production change: throwaway compose with `internal: true` + proxy sidecar, prove discord.py 2.x REST **and gateway websocket** both traverse the proxy (`proxy=` argument). Gateway-over-proxy support in discord.py has historically been shaky; on an internal network a gateway failure is a *total outage*, so this is a go/no-go gate, not a rollout-time smoke test.
  *If the spike fails:* the fallback must still remove the direct route for the whole container (e.g. `DOCKER-USER` egress DROP for the container's interface with a maintained IP set). The previously-drafted "phase-1-lite: HTTPS_PROXY for `claude` only, container keeps a direct route" is **rejected as unsafe** — an env var is advisory (`unset HTTPS_PROXY` defeats it); it must never be presented as a boundary.
- **Enforcement: compose `internal` network + CONNECT-proxy sidecar, not host iptables.**
  The bridge container moves to an `internal: true` compose network (no direct route out). A minimal egress-proxy sidecar (tinyproxy/squid, hostname allow-list, default-deny) straddles the internal and outbound networks. The bridge and `claude` reach the world only via `HTTPS_PROXY`; the proxy resolves DNS itself. **`HTTPS_PROXY` is plumbing, not a control** — the security boundary is the absent route, full stop.
  *Why over `DOCKER-USER` iptables:* iptables matches IPs; `api.anthropic.com` addresses rotate. A hostname allow-list proxy is deterministic and reviewable in-diff (via `docker-compose.example.yml` + tracked proxy config — the real compose is gitignored). `DOCKER-USER` stays documented as optional belt-and-suspenders.
- **Allow-list contents (phase 1):** `api.anthropic.com`, the **empirically-confirmed OAuth-refresh host** (verify by forcing a token refresh through the proxy and reading the proxy log before committing — do not trust the `console.anthropic.com` guess), `gateway.discord.gg`, `discord.com`, `cdn.discordapp.com`. Set `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` in the subprocess env so telemetry/statsig endpoints need not be opened.
- **Proxy deny-logging ships with phase 1.** Blocked CONNECTs are logged and surfaced (at minimum: proxy logs retained + a runbook grep; ideally a startup notice when denials involving `*.anthropic.com` appear). Rationale: a missing Anthropic-family host manifests hours later as token-expiry → `CANNOT_RUN` retry loop — the canary_oauth_crashloop failure class — and must be diagnosable in one look.
- **Egress canary — three probes, fail-closed (extends OV1):** at startup, in order:
  1. **Direct** TCP/HTTPS to a control host (e.g. `example.com`) MUST fail → proves the route is gone. Reachable → `OPEN_EGRESS` → refuse to serve (SystemExit), same posture as `DENY_DROPPED`.
  2. Control host **via the proxy** MUST be denied (proxy 403, not a tunnel) → proves the allow-list is default-deny. Tunneled → `OPEN_PROXY` → refuse to serve. *Without this probe an allow-all proxy ACL passes the canary — connectivity theater, not containment.*
  3. `api.anthropic.com` via proxy MUST succeed → `ANTHROPIC_DOWN` on failure → backoff retry (transient, no security meaning; reuse the existing retry loop shape).
  Pure classifier `classify_egress(control_direct, control_via_proxy, anthropic_via_proxy)` → `OK / OPEN_EGRESS / OPEN_PROXY / ANTHROPIC_DOWN`, unit-testable without I/O. Runs before the settings canary; uses no Claude quota.
- **Phase 2 (after `bridge-restructure`):** split into `discord-frontend` (proxy allow-list: Discord hosts only; holds bot tokens, no Claude credentials) and `executor` (proxy allow-list: Anthropic hosts only; holds credentials, no Discord tokens), IPC over a unix socket on a shared volume. Each secret then lives in a container whose egress cannot reach the place the *other* secret is useful. **Each container runs its own fail-closed egress canary asserting its own deny direction** — in particular the executor proves Discord hosts are unreachable *from inside the executor* (the container where the credential lives). A frontend-only canary proves nothing about the executor.
- **apiKeyHelper over env injection (honest framing):** in API-key mode, drop `ANTHROPIC_API_KEY` injection in `build_subprocess_env`; each bot config dir carries an `apiKeyHelper` script emitting the key. The key file **does live on the container fs** (the subprocess sees the whole container filesystem — mount isolation protects the host, not the container's own disk); its path joins the credential-read deny family, which is name-based and evadable. Net effect: removes the most obvious sink (env), replaces `printenv` with a cat/exec-helper vector behind a deny. Real, marginal, independent of the egress work; does not touch the subscription OAuth file.
- **No WebSearch change.** A prior draft "gave back" a `WebSearch` deny that does not exist in settings.json. Deleted.

## Risks / Trade-offs

- [Gateway-over-proxy fails] → caught by the Step-0 spike before anything ships; fallback is route-removing only (never env-only).
- [Proxy becomes a single point of failure] → compose healthcheck + `depends_on`; bridge's existing canary retry loop tolerates transient unavailability.
- [Anthropic endpoint drift breaks calls] → probe 3 catches API drift at startup; deny-logging catches refresh-host drift at runtime loudly.
- [Proxy container itself compromised] → runs no agent code, mounts nothing, default-deny config.
- [Reviewability of gitignored compose] → `docker-compose.example.yml` + tracked proxy config are the reviewed artifacts; runbook step diffs real against example.

## Migration Plan

0. Step-0 spike (gateway-over-proxy) — go/no-go.
1. Ship phase 1 (network + proxy + deny-logging + three-probe canary + apiKeyHelper) as one deploy; verify canary green and a normal `@`-mention round-trip; force an OAuth refresh and confirm it traverses the proxy.
2. Phase 2 lands after `bridge-restructure`, reusing the proxy pattern per container, each with its own canary.
3. Rollback: revert compose (containers rejoin default network); no settings.json involvement.
