## Why

The credential-exfiltration containment is name-based (`Bash(curl:*)` / `Bash(wget:*)` / `WebFetch` denies in settings.json) and SECURITY.md §6 already concedes it is bypassable (`/usr/bin/cu*rl`, `python -c`, `/proc/self/environ`). The live OAuth credentials are the deployment's highest-value secret, and the only thing standing between a fully-bypassed agent and exfiltration is command-name matching. Network-layer egress containment closes the *autonomous* (injection-driven) exfil class: a bypassed agent that reads the credential has no network path to send it — **fully so only at phase 2**, when the executor's egress is Anthropic-only. It is also the precondition for granting the execution tier real shell capability (see `agent-exec-loop`, whose Bash milestone gates on phase 2 of this change).

**Honest scope statement (review D2):** phase 1 (single container) allow-lists Discord hosts, and `discord.com` webhooks / `cdn.discordapp.com` uploads are a universal exfil sink — so phase 1 is a *migration scaffold* (network plumbing, proxy, canary), not credential containment. And no egress control addresses the bypass-*user* adversary, who can simply have the agent print a credential into its reply; that residual is accepted and documented (SECURITY.md §3/§7).

## What Changes

- **Blocking prerequisite spike**: prove discord.py 2.x gateway websocket connects through a CONNECT proxy from an `internal: true` network (throwaway compose). Load-bearing for everything below — if the gateway can't traverse the proxy, the bot is fully down, and the only acceptable fallback is one that still removes the direct route (see design).
- **Network egress allow-list, phase 1 (scaffold, single container)**: bridge container moves to an `internal: true` compose network; a default-deny hostname-allow-list CONNECT-proxy sidecar is the only way out. Collapses the exfil surface from "anywhere" to "Anthropic or Discord" (the latter still being a usable sink — see above).
- **Network egress allow-list, phase 2 (target, split containers)**: split into a `discord-frontend` container (Discord egress only, holds bot tokens, no Claude credentials) and an `executor` container (Anthropic egress only, runs `claude -p`), communicating over a unix socket / shared volume. **This is where the credential-containment claim becomes true.** Depends on `bridge-restructure` (runner module extraction).
- **Egress canary (three probes, fail-closed)**: extend the startup canary (OV1) to prove containment, not connectivity: (1) direct connect to a control host MUST fail (route removed), (2) control host **via the proxy** MUST be denied (allow-list is actually default-deny — catches the allow-all-ACL misconfig), (3) Anthropic via proxy MUST succeed. Any other state → refuse to serve / retry per class.
- **OAuth-refresh host pinned empirically before the allow-list is committed**: watch a forced token refresh through the proxy; a wrong guess means "green deploy, CANNOT_RUN loop hours later when the token expires" (the canary_oauth_crashloop class, invisible at deploy time). Proxy deny-logging ships with phase 1 so any blocked Anthropic-family host is loud, not a silent restart loop.
- **apiKeyHelper for API-key mode (independent hardening)**: replace per-bot env injection (`ANTHROPIC_API_KEY_{A,B}`) with `apiKeyHelper`, so the key never sits in the subprocess env (SPEC §11 backlog item). The key file still exists on the container fs and is guarded only by the name-based Read deny — this is a real but *marginal* improvement, and it does not touch the subscription-mode OAuth file at all (that one's mitigation is egress, phase 2).
- Correct the scope pointer: the "egress is out of scope" acknowledgment being retired lives in **SECURITY.md §7** (not `docs/m1-host-remediation.md`, which is about inbound exposure).

## Capabilities

### New Capabilities
- `egress-containment`: network-layer egress allow-list for the execution path, proven in force by a three-probe startup canary (route removed + proxy default-deny + Anthropic reachable); fail-closed when unproven.

### Modified Capabilities
- `execution-permissions`: the deny family's role changes from *sole* exfil barrier to defense-in-depth behind the network block. (No deny-list entries are removed — a prior draft claimed a "WebSearch un-deny", but `WebSearch` was never in the deny list; that strand is deleted.)
- `bot-identity-isolation`: API-key mode credentials move from subprocess env to `apiKeyHelper`; the credential-exposure statement in the deployment template is updated to reflect the network-layer bound and its phase-1 limits.

## Impact

- **Code**: `bot.py` (three-probe egress canary, apiKeyHelper wiring, env-injection removal, proxy env for bridge + subprocess), `docker-compose.yml` **and `docker-compose.example.yml`** (networks + proxy sidecar; the real compose is gitignored, so the example file is the reviewable artifact and MUST carry the same enforcement structure), `Dockerfile` if the proxy needs config baked, new runbook section in tracked docs.
- **Host**: `DOCKER-USER` iptables rules stay documented as optional belt-and-suspenders only.
- **Dependencies**: prerequisite spike blocks phase 1; phase 2 depends on `bridge-restructure`. `agent-exec-loop`'s Bash-tier relaxation depends on **phase 2** (per review D3), not merely a green phase-1 canary.
- **Out of scope**: OS-level sandboxing inside the container (seccomp/AppArmor profiles), disposable-account rotation, and the reply-channel exfil residual (documented, accepted).
