## 0. Blocking prerequisite spike

- [x] 0.1 Throwaway compose: `internal: true` network + CONNECT-proxy sidecar; prove discord.py 2.x REST **and gateway websocket** connect via `proxy=` (go/no-go — nothing below ships until this passes)
  - **GO (2026-07-06).** discord.py 2.4.0 (repo pin) on python:3.12-slim: `discord.Client(intents=..., proxy="http://proxy:8888")` covers both REST and gateway; no env vars / proxy_auth / aiohttp patches. Proxy: `alpine:3.20 + apk add tinyproxy`, `ConnectPort 443`, `Filter` BRE allow-list (`^discord\.com$`, `^gateway\.discord\.gg$`), `FilterDefaultDeny Yes`, `LogLevel Connect`. Evidence: direct egress = `OSError(101)` (route absent); `CONNECT example.com` → `403 Filtered`; `CONNECT discord.com`/`gateway.discord.gg` established, `on_ready` + `application_info()` OK. `LogLevel Connect` already yields the grep-able deny-log lines task 1.6 needs; scope `Allow` to the internal subnet in prod.
- [x] 0.2 If the spike fails: document the route-removing fallback — **N/A, spike passed**; iptables fallback not needed, compose-internal + tinyproxy sidecar proceeds as designed. (Env-only "proxy for claude only" fallback remains rejected as unsafe.)

## 1. Egress proxy + network (phase 1)

- [x] 1.1 **OPERATOR CUTOVER STEP** — DONE 2026-07-08: live compose switched to phase-1 topology, three-probe canary green, forced refresh (scripts/expire-oauth-token.sh, host-side through the published proxy port) observed contacting **api.anthropic.com only** with the token persisted (+8h). `console.anthropic.com` never attempted → removed from the filter. Denied-and-harmless during the run: `mcp-proxy.anthropic.com` (MCP connectors, noisy retries), `http-intake.logs.us5.datadoghq.com` (CLI telemetry). Recorded in SECURITY.md §6.
- [x] 1.2 Add the egress-proxy sidecar (default-deny hostname allow-list) — `egress-proxy/{Dockerfile,tinyproxy.conf,filter}`, wired in `docker-compose.example.yml`; allow-list: `api.anthropic.com`, `console.anthropic.com` (pending 1.1 confirmation), `gateway.discord.gg`, `discord.com`, `cdn.discordapp.com`. Functionally verified: allow-listed → `200 established`, `example.com` → `403 Filtered`.
- [x] 1.3 Move the bridge container onto an `internal: true` network; proxy straddles internal + outbound networks (example.yml; live cutover is 1.1's operator step)
- [x] 1.4 Set `HTTPS_PROXY`/`HTTP_PROXY` in `build_subprocess_env` for `claude` + `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, gated on `config.EGRESS_PROXY_URL` (in-code note: proxy env is plumbing; the boundary is the internal network)
- [x] 1.5 Pass `proxy=config.EGRESS_PROXY_URL` to `discord.Client` (covers REST + gateway per the spike)
- [x] 1.6 Proxy healthcheck (`netstat` on :8888) + `depends_on: service_healthy`; deny-logging via `LogLevel Connect` (grep-able), documented in SECURITY.md
- [x] 1.7 Full network/proxy structure mirrored into `docker-compose.example.yml` (reviewable artifact) + tracked `egress-proxy/` config. Real gitignored compose left for the operator cutover (1.1).

## 2. Egress canary (three probes, fail-closed)

- [x] 2.1 Pure `classify_egress(control_direct, control_via_proxy, anthropic_via_proxy)` → EGRESS_OK / EGRESS_OPEN / EGRESS_OPEN_PROXY / EGRESS_ANTHROPIC_DOWN (bridge/egress.py, no I/O)
- [x] 2.2 `bot.main` runs `run_egress_canary` before the settings canary when `EGRESS_PROXY_URL` is set: OPEN_EGRESS / OPEN_PROXY → SystemExit; ANTHROPIC_DOWN → backoff retry (reuses the canary loop shape). Skipped when no proxy configured (uncontained deploy).
- [x] 2.3 Unit tests for all four classifications, incl. the allow-all-proxy state (tests/test_egress.py)

## 3. apiKeyHelper (API-key mode — independent hardening) — DONE

> Deferral resolved 2026-07-08: the shadowing risk was specific to putting apiKeyHelper
> in the SHARED `--settings` file. Implementation avoids it by construction — the helper
> lives in each bot's **config-dir** settings.json and is provisioned **only when
> `USE_API_KEY` is set** (subscription mode writes nothing). Live-verified that a
> config-dir apiKeyHelper is consulted by `claude -p` (fake key → "Invalid API key").

- [x] 3.1 `runner.provision_api_key_helper` (called from bot.main, gated on USE_API_KEY): 0600 `anthropic-api-key` + executable `api-key-helper.sh` per config dir; config-dir settings.json merged, fail-loud on corruption
- [x] 3.2 `ANTHROPIC_API_KEY` injection removed from `build_subprocess_env`; deny-list scrub of the whole family unchanged (subprocess env now key-free in BOTH modes)
- [x] 3.3 `Read(//home/user/**/anthropic-api-key)` added to the settings.json deny family
- [x] 3.4 `validate_config`: API-key mode accepts an env key OR a pre-dropped key file per bot; neither → SystemExit
- [x] 3.5 Tests (test_apikey_helper.py, test_env_scrub.py): env key-free in both modes; provisioning (perms/exec/settings-merge/corrupt-refuse/file-seeded/no-source-refuse); validate_config file-seeded accept + no-source exit; subscription-mode zero side effects

## 4. Docs

- [x] 4.1 SECURITY.md §6/§7 updated: phase 1 = scaffold (Discord webhook sink named), credential containment claimed at phase 2 only, reply-channel residual stated, deny family = defense-in-depth, three-probe canary documented
- [x] 4.2 Scope pointer fixed: SECURITY.md §7 "egress unrestricted" retired → phase-1 contained/uncontained; operator cutover + `DOCKER-USER` belt-and-suspenders noted in §6
- [x] 4.3 Test: deny family unchanged by this change (credential/env/WebFetch present, WebSearch absent) — tests/test_egress.py

## 5. Phase 2 (split containers — after bridge-restructure)

- [ ] 5.1 Split compose into `discord-frontend` (Discord-only egress, bot tokens) and `executor` (Anthropic-only egress, credentials); IPC over unix socket on a shared volume
- [ ] 5.2 Per-container proxy allow-lists (Discord hosts vs Anthropic hosts)
- [ ] 5.3 **Per-container canaries**: each container runs its own three-probe fail-closed canary asserting its own deny direction — executor proves Discord unreachable *from the executor*; frontend proves Anthropic unreachable from the frontend
- [ ] 5.4 Tests/smoke: full pytest; example.yml mirrors the split; live smoke (both canaries green, `@`-mention round-trip, forced OAuth refresh traverses executor proxy)
