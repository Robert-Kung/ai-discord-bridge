## Context

`bot.py` is 1,652 lines holding config, memory engine, execution chokepoint + canary, approver IPC, debate orchestration, and Discord I/O — plus ~14 module-level mutable structures (locks, `channel_msg_log`, `pending_actions`, `bot_user_ids`, `session_ctx_tokens`, `bot_turns_since_human`, …) that any split must assign an owner. The trust guarantees (single chokepoint, conversation-layer cannot escalate) are currently enforced by comments and discipline within one file. Two queued changes (`agent-exec-loop`, `egress-exec-isolation` phase 2) both need the runner separable. The test suite (`tests/`, ~1k lines across 10 files) pins the security-critical behavior and is the safety net — but see Test Migration below: the SRC-scan tests must be rewritten, not just re-pointed.

## Goals / Non-Goals

**Goals:**
- Split into a `bridge/` package along the existing trust-layer seams.
- Preserve every spec-level behavior verbatim (pure refactor).
- Turn the chokepoint invariants into **package-wide, mechanically-checked** facts (AST import allowlist), not in-file conventions.
- Explicit ownership for every module-level mutable and every currently-unplaced symbol.

**Non-Goals:**
- Any behavior change, new feature, or dependency (those are the other two changes).
- A `BridgeState` object / dependency-injection rework — deferred to `agent-exec-loop` (its job registry is the right moment); this change uses a plain `bridge/state.py`.

## Decisions

- **Package layout by trust seam:** `bridge/{config,state,sessions,runner,memory,trust,approver_ipc,discuss,frontend}.py`; `bot.py` becomes the entrypoint.
  - `config.py` — env loading, `load_config`/`validate_config`, `_CONFIG_GLOBALS`, constants/thresholds (dependency-free leaf).
  - `state.py` — the shared mutables: `bot_locks`, `turn_lock`, `cwd_locks`, `discuss_locks`, `channel_msg_log`, `messages_since_flush`, `session_ctx_tokens`, `token_checkpointed`, `pending_actions`, `bot_user_ids`, `clients`, `bot_turns_since_human`. Each carries a **single-writer note** (who mutates, who reads). `bot_turns_since_human` is a bare `int` rebound via `global` — it must be wrapped (module-level accessor or one-slot holder), it cannot be from-imported.
  - `sessions.py` — session + channel-state persistence (`load_session`/`save_session`/`clear_session`, `load_channel_state`/`save_channel_state`/`get_channel_cwd`, `_cwd_slug`, path helpers). Leaf over `config`.
  - `runner.py` — `build_claude_args`, `_run_claude_subprocess`, `_call_claude`, `converse`/`execute`, `exec_layer_for`, canary. Imports `config`/`state`/`sessions` only — **no `memory`, no Discord** (see cycle-break below).
  - `memory.py` — buffer layer (`buffer_append`, `record_bot_reply`, `format_buffer_transcript`, `build_context_prefix`), summaries, project notes, `do_flush`, `flush_session_then_reset`, `maybe_token_flush`, `build_combined_system_prompt`. May import `runner.converse` (one-directional).
  - `trust.py` — `_is_trusted`, `bypass_allowed`/`approve_allowed`/`_tier_allowed`, mode maps, `resolve_project_cwd` (it is a security guard: cwd whitelist + `.git` check).
  - `approver_ipc.py` — approval socket server. It does NOT depend on the runner; it needs a human-approval round-trip, which is frontend turf. It receives an injected `ask_human(command, tool_name) -> bool` callable at server start; `frontend` owns `pending_actions` and provides that callable (today's `request_discord_approval`).
  - `discuss.py` — `run_discuss` (conversation layer: imports `converse` only).
  - `frontend.py` — Discord clients, command handlers, message routing, `run_plan_then_execute`, `chunk_message`/parsers, `HELP_TEXT`/`STARTUP_ANNOUNCEMENT`, `read_cswap_usage`; thin I/O + routing, the only module that may import `execute`.
  *Alternative considered:* shrink to `config/state/runner/trust` only and let `agent-exec-loop` shape the rest — rejected at review gate (D1): both queued changes also consume the frontend/memory boundaries, and the suite pins behavior either way; do the full split once, with the design gaps below fixed first.
- **Cycle break (runner ↔ memory):** today `_call_claude` calls `build_combined_system_prompt` (bot.py:697) while `do_flush`/`flush_session_then_reset` call `converse` (bot.py:863,906) — a hard cycle as previously drafted. Resolution: `_call_claude`/`converse`/`execute` accept an optional `system_prompt_file` **path**; the *callers* (frontend, discuss) build it via `memory.build_combined_system_prompt`. `prepend_summary_from_channel` disappears from the runner signature. `memory → runner` is then the only edge.
- **Config access rule (staleness guard):** `load_config()` rebinds module globals, so `from bridge.config import CHANNEL_ID` would capture the pre-load default **permanently** (silently dead bot: never answers, canary green). Mandate **attribute access** (`config.CHANNEL_ID`) everywhere; an AST test forbids `from bridge.config import <any _CONFIG_GLOBALS name>` package-wide. Same rule for `state` members.
- **`__file__`-anchored paths re-anchored:** `BRIDGE_SETTINGS_PATH` fallback, `_HERE`/`APPROVER_SCRIPT`/`APPROVER_ALLOWLIST_PATH` (bot.py:88–102) currently resolve relative to `bot.py`; moved into `bridge/config.py` they would silently point at `bridge/…`. Anchor on the repo root explicitly, and add a startup assertion that `APPROVER_SCRIPT` exists when the approver tier is enabled (the default-mode canary cannot catch a dead approve tier).
- **Package-wide import allowlist replaces the single-file grep.** "frontend must not reference `_call_claude`" is not the guarantee — frontend legitimately imports `execute()` (free `mode` param); escalation is prevented by call-site logic that stays behavior-tested. The mechanical invariant worth pinning, via AST over the whole package: only `frontend` imports `execute`; only `runner` contains `create_subprocess_exec` and the `"claude", "-p"` argv literal; `discuss`/`memory` import `converse` at most; nothing outside `runner` imports `_call_claude`/`build_claude_args`.
- **Test migration is a rewrite for the SRC-scan tests, not a re-point.** `tests/test_layers.py` reads `bot.py` source text and asserts counts/positions (`SRC.count("create_subprocess_exec(") == 1`, …). After the split those must scan the `bridge/` package, or a second subprocess call added to `frontend.py` later passes every test. Additionally: every `monkeypatch.setattr` target must be re-anchored to the **consuming** module (patching a `bot.py` re-export can make fail-closed security tests pass **vacuously**); each security gate keeps one positive-direction assertion; `bot.py` does not re-export split names.
- **Dockerfile ships the package.** `Dockerfile:20` COPYs individual files only; "Dockerfile untouched" was wrong — without `COPY bridge/ /app/bridge/` the image dies with `ModuleNotFoundError` under `restart: unless-stopped` (tight crash-loop). Verification must be `docker compose build` + an import smoke run, not `docker compose config` (which neither builds nor runs).
- **One commit-worthy checkpoint per module move**, running the full suite between moves, so drift is caught immediately.

## Risks / Trade-offs

- [Refactor reintroduces a chokepoint bypass] → package-wide AST allowlist test + rewritten package-scan chokepoint tests + existing layer/permission behavior tests; full suite after each extraction.
- [Config/state staleness via from-imports] → attribute-access rule + AST test; the failure mode is silent (fail-closed but dead), so the test is mandatory, not advisory.
- [Vacuous security tests after patch-target churn] → explicit monkeypatch-target audit task + positive-direction assertions.
- [Circular imports] → dependency direction fixed above: `config`/`state`/`sessions` leaves → `runner`/`trust` → `memory`/`discuss` → `approver_ipc`/`frontend`.

## Migration Plan

- Extract bottom-up: `config` → `state`/`sessions` → `runner`/`trust` → `memory`/`discuss` → `approver_ipc`/`frontend`; full suite green at each step.
- `bot.py` keeps working as the entrypoint (`python bot.py`); Dockerfile gains the `bridge/` COPY in the same change.
- Rollback: single revert restores the monolith; no data or interface change.
