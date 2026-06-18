# M4 approver preflight (probe before building, like gates 0.1/0.2)

Empirically verified 2026-06-18 against claude **2.1.181** with a hand-rolled minimal
MCP stdio server (`--permission-prompt-tool mcp__approver__approve --mcp-config … --strict-mcp-config`),
isolated `CLAUDE_CONFIG_DIR=~/.claude-b`. **M4 is viable, but the wiring differs from
the task's assumption — it must run under `default` mode, not `acceptEdits`.**

## What works
- **The approver enforces deny.** A command the server returned `{"behavior":"deny"}` for
  (`touch …DENYME…`) did **not** execute — file not created, and it appeared in the
  result's `permission_denials`. So an MCP approver IS the restrictive deny-by-default
  boundary headless otherwise lacks (the thing gate 0.1 showed `--allowedTools` can't do).
- **The MCP contract (captured):**
  - Request: `tools/call`, `params = {name:"approve", arguments:{tool_name, input:{command, description}, tool_use_id}, _meta:{...}}`
  - Response: tool result `content:[{type:"text", text:"<json>"}]` where `<json>` is
    `{"behavior":"allow","updatedInput":{…}}` or `{"behavior":"deny","message":"…"}`
  - Server lifecycle: standard `initialize` → `notifications/initialized` → `tools/list` →
    `tools/call`. Client sends `protocolVersion:"2025-11-25"`, `clientInfo.name:"claude-code"`.

## The two gotchas (must drive the M4 design)
1. **`acceptEdits` does NOT consult the approver.** Under `--permission-mode acceptEdits`
   the same `touch …DENYME…` ran with **zero** `tools/call` to the server — acceptEdits
   auto-accepts Bash, so the approver is dead. **The approver tier must use
   `--permission-mode default`** (the mode that withholds permission and routes to the
   prompt tool). This is a NEW execution mode distinct from the current `edit` tier.
2. **"Safe" read-only commands auto-pass without the approver.** Even under `default`,
   `whoami` ran without a `tools/call` — claude auto-allows commands it classifies as
   read-only/safe; only state-changing/dangerous ones (writes, `touch`, presumably exec
   and network) reach the approver. Acceptable: the `permissions.deny` family still blocks
   credential reads / env / network by name, and the approver gates the dangerous set. We
   cannot fully control claude's safe-command classifier, so deny-family stays the backstop.

## Resulting M4 design (refined)
- Approver tier = `--permission-mode default` + `--permission-prompt-tool` + `--settings`
  (deny family still applies) — a NEW tier, stricter than `edit`, replacing the use of
  `bypass`. `edit` (acceptEdits, deny-contained, no per-command approval) stays as-is.
- The MCP server is **policy + human**:
  - `tool_name in {Read}` or `input.command` matches the **operator allow-list** (below)
    → auto-`allow` (no Discord round-trip; the deferred restrictive allow-list finally
    lives here as deny-by-default).
  - `Edit`/`Write` within a mounted project dir → auto-`allow` (or gate, operator choice).
  - anything else → post to Discord, await ✅/❌ from an `ALLOWED_USER_IDS` reaction;
    **timeout / malformed / server-down → `deny`** (fail closed).
- Reuse the existing `on_raw_reaction_add` whitelist gate for approvals.

## Probe artifacts
`/tmp/mcp_probe/server.py` (minimal approver), `cfg.json`, `requests.log`. The server is a
throwaway probe, not the M4 implementation.
