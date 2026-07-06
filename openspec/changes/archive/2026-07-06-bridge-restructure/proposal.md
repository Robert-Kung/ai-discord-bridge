## Why

`bot.py` is 1,652 lines carrying five concerns at once — Discord I/O, the four-layer memory engine, the execution chokepoint + canary, the approver IPC, and the debate orchestrator. That violates the project's own thin-wrapper/service-layer principle, and it is the practical blocker for the two changes queued behind it: `agent-exec-loop` needs to rework the runner without touching trust logic, and `egress-exec-isolation` phase 2 needs the runner extractable into its own container. Restructure now, while the test suite (10 files, ~1k lines) pins the security-critical behavior.

## What Changes

- **Pure refactor — no behavior change.** Split `bot.py` into a `bridge/` package with module boundaries matching the existing trust layers:
  - `bridge/config.py` — env loading, `load_config`/`validate_config`, constants (attribute-access only; see design).
  - `bridge/state.py` — the shared module-level mutables (locks, buffers, `pending_actions`, `bot_user_ids`, …) with single-writer ownership notes.
  - `bridge/sessions.py` — session + channel-state persistence.
  - `bridge/runner.py` — the execution chokepoint: `build_claude_args`, `_run_claude_subprocess`, `_call_claude`, `converse`/`execute`, `exec_layer_for`, canary. The one module a future executor container imports; no `memory`, no Discord.
  - `bridge/memory.py` — buffer layer, summaries, project notes, flush logic, token thresholds, combined system prompt.
  - `bridge/trust.py` — `_is_trusted`, tier gates (`bypass_allowed`/`approve_allowed`/`_tier_allowed`), `resolve_project_cwd`.
  - `bridge/approver_ipc.py` — approval socket server (human round-trip injected by frontend).
  - `bridge/discuss.py` — debate orchestration.
  - `bridge/frontend.py` — Discord clients, command handlers, message routing, `run_plan_then_execute`; thin I/O + routing, the only importer of `execute`.
  - `bot.py` shrinks to the entrypoint (`main()` wiring).
- **Tests migrate, suite stays green**: behavior assertions unchanged, but (a) the SRC-scan chokepoint tests (`test_layers.py` source-text counts) are rewritten to scan the whole `bridge/` package, and (b) every monkeypatch target is re-anchored to the consuming module so no security test can pass vacuously.
- **Structural guarantees become mechanically checked**: a package-wide AST import allowlist pins "only `frontend` imports `execute`; only `runner` launches subprocesses; `discuss`/`memory` import `converse` at most". (The escalation guard itself — `exec_layer_for` routing, tier gates — stays behavior-tested; imports alone cannot express it.)

## Capabilities

### New Capabilities
- `bridge-module-boundaries`: business logic lives in service modules; the Discord frontend is a thin I/O wrapper; the execution chokepoint is importable in isolation from Discord.

### Modified Capabilities
<!-- None. All existing requirements (agent-trust-layers, execution-permissions, bot-identity-isolation) are preserved verbatim — this change relocates their implementation without altering any spec-level behavior. -->

## Impact

- **Code**: `bot.py` → `bridge/` package split; `tests/*` import paths + monkeypatch targets; `Dockerfile` gains `COPY bridge/ /app/bridge/` (entrypoint `python bot.py` unchanged — but without the COPY the image crash-loops with `ModuleNotFoundError` under `restart: unless-stopped`).
- **Risk**: refactor drift reintroducing a bypass around the chokepoint — mitigated by the existing layer/permission tests plus a package-wide AST import-allowlist test and package-scan chokepoint tests; silent config staleness (from-import of `_CONFIG_GLOBALS`) guarded by a dedicated AST test.
- **Sequencing**: lands before `agent-exec-loop` and before `egress-exec-isolation` phase 2 (both are consumers of the split). No runtime dependencies; independently shippable.
