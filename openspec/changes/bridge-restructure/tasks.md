## 1. Extract leaf modules

- [ ] 1.1 `bridge/config.py`: env loading, `load_config`/`validate_config`, `_CONFIG_GLOBALS`, constants, thresholds (dependency-free leaf); re-anchor `BRIDGE_SETTINGS_PATH` fallback + `_HERE`/`APPROVER_SCRIPT`/`APPROVER_ALLOWLIST_PATH` to the repo root (not `__file__` of the new module)
- [ ] 1.2 `bridge/state.py`: `bot_locks`, `turn_lock`, `cwd_locks`, `discuss_locks`, `channel_msg_log`, `messages_since_flush`, `session_ctx_tokens`, `token_checkpointed`, `pending_actions`, `bot_user_ids`, `clients`; wrap `bot_turns_since_human` (bare int, `global`-rebound — accessor/holder, never from-imported); single-writer note per structure
- [ ] 1.3 `bridge/sessions.py`: `load_session`/`save_session`/`clear_session`, `load_channel_state`/`save_channel_state`/`get_channel_cwd`, `_cwd_slug`, `channel_state_path`/`_session_path`
- [ ] 1.4 Full suite green after each move

## 2. Extract service modules

- [ ] 2.1 `bridge/runner.py`: `build_claude_args`, `_run_claude_subprocess`, `_call_claude`, `converse`, `execute`, `exec_layer_for`, `build_subprocess_env`, canary (`classify_canary`/`canary_passed`/`run_settings_canary`) — imports `config`/`state`/`sessions` only, no `memory`, no Discord
- [ ] 2.2 Cycle break: `_call_claude`/`converse`/`execute` take optional `system_prompt_file` path; drop `prepend_summary_from_channel` from the runner; callers build the file via `memory.build_combined_system_prompt`
- [ ] 2.3 `bridge/memory.py`: buffer layer (`buffer_append`, `record_bot_reply`, `format_buffer_transcript`, `build_context_prefix`), summaries (`save_summary`/`latest_summary_path`/`channel_summary_dir`), project notes, `do_flush`, `flush_session_then_reset`, `maybe_token_flush`, `build_combined_system_prompt` — may import `runner.converse` (one direction)
- [ ] 2.4 `bridge/trust.py`: `_is_trusted`, `bypass_allowed`/`approve_allowed`/`_tier_allowed`, mode maps, `resolve_project_cwd`
- [ ] 2.5 Config/state access rule: convert all cross-module uses to attribute access (`config.X` / `state.Y`); no from-imports of `_CONFIG_GLOBALS` or state members
- [ ] 2.6 Full suite green after each move

## 3. Extract orchestration + IPC

- [ ] 3.1 `bridge/approver_ipc.py`: approval socket server taking an injected `ask_human(command, tool_name) -> bool` callable; `frontend` owns `pending_actions` and supplies `request_discord_approval` as that callable
- [ ] 3.2 `bridge/discuss.py`: `run_discuss` (imports `converse` only)
- [ ] 3.3 Full suite green after each move

## 4. Frontend + entrypoint

- [ ] 4.1 `bridge/frontend.py`: `make_client`, command handlers, message routing, `run_plan_then_execute`, `request_discord_approval`, `chunk_message`, `parse_command`/`extract_once_override`/`extract_yolo_flag`, `HELP_TEXT`/`STARTUP_ANNOUNCEMENT`, `read_cswap_usage` — thin I/O + routing; the only module importing `execute`
- [ ] 4.2 `bot.py` reduced to `main()` wiring/entrypoint; `python bot.py` still runs; no re-exports of split names
- [ ] 4.3 Startup assertion: `APPROVER_SCRIPT` exists when the approver tier is enabled
- [ ] 4.4 Re-point test imports to the new modules; audit every `monkeypatch.setattr` target to the **consuming** module; keep one positive-direction assertion per security gate (no vacuous passes)

## 5. Guard + verify

- [ ] 5.1 Package-wide AST import-allowlist test: only `frontend` imports `execute`; only `runner` contains `create_subprocess_exec` + the `"claude", "-p"` literal; `discuss`/`memory` import `converse` at most; nothing outside `runner` references `_call_claude`/`build_claude_args`
- [ ] 5.2 AST test forbidding `from bridge.config import <_CONFIG_GLOBALS name>` (and state members) package-wide
- [ ] 5.3 Rewrite the SRC-scan chokepoint tests (`tests/test_layers.py` count/position assertions) to scan the whole `bridge/` package
- [ ] 5.4 Test: `bridge/runner.py` imports without pulling in the Discord frontend
- [ ] 5.5 Dockerfile: add `COPY bridge/ /app/bridge/`
- [ ] 5.6 Verify: full pytest green; `docker compose build` succeeds AND `docker run --rm --entrypoint python3 <img> -c "import bot"` passes (import is side-effect-free); behavior unchanged per the security tests
