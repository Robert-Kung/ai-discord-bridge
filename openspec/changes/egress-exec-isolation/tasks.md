## 0. Blocking prerequisite spike

- [x] 0.1 Throwaway compose: `internal: true` network + CONNECT-proxy sidecar; prove discord.py 2.x REST **and gateway websocket** connect via `proxy=` (go/no-go — nothing below ships until this passes)
  - **GO (2026-07-06).** discord.py 2.4.0 (repo pin) on python:3.12-slim: `discord.Client(intents=..., proxy="http://proxy:8888")` covers both REST and gateway; no env vars / proxy_auth / aiohttp patches. Proxy: `alpine:3.20 + apk add tinyproxy`, `ConnectPort 443`, `Filter` BRE allow-list (`^discord\.com$`, `^gateway\.discord\.gg$`), `FilterDefaultDeny Yes`, `LogLevel Connect`. Evidence: direct egress = `OSError(101)` (route absent); `CONNECT example.com` → `403 Filtered`; `CONNECT discord.com`/`gateway.discord.gg` established, `on_ready` + `application_info()` OK. `LogLevel Connect` already yields the grep-able deny-log lines task 1.6 needs; scope `Allow` to the internal subnet in prod.
- [x] 0.2 If the spike fails: document the route-removing fallback — **N/A, spike passed**; iptables fallback not needed, compose-internal + tinyproxy sidecar proceeds as designed. (Env-only "proxy for claude only" fallback remains rejected as unsafe.)

## 1. Egress proxy + network (phase 1)

- [ ] 1.1 Force an OAuth token refresh through the proxy and record the actual host(s) from the proxy log; pin the allow-list to observed reality (do not trust the `console.anthropic.com` guess)
- [ ] 1.2 Add the egress-proxy sidecar (default-deny hostname allow-list) to compose; allow-list: `api.anthropic.com`, confirmed OAuth-refresh host, `gateway.discord.gg`, `discord.com`, `cdn.discordapp.com`
- [ ] 1.3 Move the bridge container onto an `internal: true` network; proxy straddles internal + outbound networks
- [ ] 1.4 Set `HTTPS_PROXY`/`HTTP_PROXY` for the bridge process and in `build_subprocess_env` for `claude`; add `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` (note in-code: proxy env is plumbing; the boundary is the internal network)
- [ ] 1.5 Pass `proxy=` to discord.py REST + gateway (per spike findings)
- [ ] 1.6 Proxy healthcheck + `depends_on`; proxy deny-logging enabled and retained, runbook grep documented
- [ ] 1.7 Mirror the full network/proxy structure into `docker-compose.example.yml` (the reviewable artifact — real compose is gitignored) + tracked proxy config

## 2. Egress canary (three probes, fail-closed)

- [ ] 2.1 Pure `classify_egress(control_direct, control_via_proxy, anthropic_via_proxy)` → OK / OPEN_EGRESS / OPEN_PROXY / ANTHROPIC_DOWN (unit-testable, no I/O)
- [ ] 2.2 Startup checks before the settings canary: direct control MUST fail; control **via proxy** MUST be denied (403, not tunneled); Anthropic via proxy MUST succeed. OPEN_EGRESS / OPEN_PROXY → SystemExit; ANTHROPIC_DOWN → backoff retry (reuse existing loop shape)
- [ ] 2.3 Unit tests for all four classifications, including the allow-all-proxy state (direct fails + proxy tunnels control → OPEN_PROXY, never OK)

## 3. apiKeyHelper (API-key mode — independent hardening)

- [ ] 3.1 Provision an `apiKeyHelper` script per bot config dir; key file outside all mounted project dirs (it remains on the container fs — documented as name-deny-guarded only)
- [ ] 3.2 Remove `ANTHROPIC_API_KEY` injection from `build_subprocess_env`; keep the deny-list scrub of the whole key family
- [ ] 3.3 Add the key file path to the credential-read deny family in settings.json
- [ ] 3.4 Update `validate_config` API-key-mode check to validate the helper is present instead of the env key
- [ ] 3.5 Tests: subprocess env has no `ANTHROPIC_API_KEY*`; subscription mode path unchanged

## 4. Docs

- [ ] 4.1 Update SECURITY.md §6/§7: phase 1 = scaffold (Discord webhook sink named explicitly); credential containment claimed at phase 2 only; reply-channel residual stated; deny family = defense-in-depth; three-probe canary documented
- [ ] 4.2 Fix the scope pointer: retire the "egress unrestricted" acknowledgment in SECURITY.md §7 (not m1-host-remediation.md, which is inbound-only); document `DOCKER-USER` as optional belt-and-suspenders in the runbook
- [ ] 4.3 Test: settings canary still proves the credential/env/WebFetch denies loaded (deny list is unchanged by this change)

## 5. Phase 2 (split containers — after bridge-restructure)

- [ ] 5.1 Split compose into `discord-frontend` (Discord-only egress, bot tokens) and `executor` (Anthropic-only egress, credentials); IPC over unix socket on a shared volume
- [ ] 5.2 Per-container proxy allow-lists (Discord hosts vs Anthropic hosts)
- [ ] 5.3 **Per-container canaries**: each container runs its own three-probe fail-closed canary asserting its own deny direction — executor proves Discord unreachable *from the executor*; frontend proves Anthropic unreachable from the frontend
- [ ] 5.4 Tests/smoke: full pytest; example.yml mirrors the split; live smoke (both canaries green, `@`-mention round-trip, forced OAuth refresh traverses executor proxy)
