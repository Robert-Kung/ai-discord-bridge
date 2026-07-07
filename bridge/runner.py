"""The execution chokepoint: argv assembly, the single subprocess launcher, the
settings canary, and the two public layer entries (converse / execute).

This module imports config/state/sessions ONLY — never memory, never the Discord
frontend — so it can be hosted in a separate executor context. Callers that want a
summary/notes system prompt build it themselves (via memory.build_combined_system_prompt)
and pass the path as `system_prompt_file`; the runner never reaches into memory,
breaking the runner↔memory cycle.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from pathlib import Path
from typing import Callable

from bridge import config, sessions, state

log = logging.getLogger("bridge.runner")


def build_subprocess_env(cfg: dict, base_env: "dict | None" = None) -> dict:
    """Env for a `claude -p` subprocess: strip the secret/auth-routing family, set
    this bot's CLAUDE_CONFIG_DIR, and — API-key mode only — inject ONLY this bot's
    own key. Pure: pass base_env in tests instead of monkeypatching os.environ."""
    src = os.environ if base_env is None else base_env
    env = {k: v for k, v in src.items() if k not in config._SUBPROCESS_ENV_DENY}
    env["CLAUDE_CONFIG_DIR"] = cfg["config_dir"]
    if config.USE_API_KEY and cfg.get("api_key"):
        env["ANTHROPIC_API_KEY"] = cfg["api_key"]
    # Egress containment (phase 1): route `claude`'s HTTPS through the allow-list proxy
    # and drop non-essential telemetry so those endpoints need not be opened. The proxy
    # env is plumbing, not the boundary — the boundary is the routeless internal network.
    if config.EGRESS_PROXY_URL:
        env["HTTPS_PROXY"] = config.EGRESS_PROXY_URL
        env["HTTP_PROXY"] = config.EGRESS_PROXY_URL
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    return env


def build_claude_args(
    api_mode: str,
    *,
    session_id: str | None = None,
    system_prompt_file: str | None = None,
    approver_mcp_config: str | None = None,
    stream: bool = False,
) -> list[str]:
    """Assemble the `claude -p` argv — the single place argv is constructed.

    Fixed order: fixed flags, then value flags (`--settings`, `--permission-mode`,
    `--resume`, `--append-system-prompt-file`) each taking exactly one argument. No
    variadic flag is emitted; the prompt is always fed via stdin (never argv).

    `stream=True` selects the incremental JSONL surface for background exec jobs:
    `--output-format stream-json` REQUIRES `--verbose` in `-p` mode (the CLI hard-fails
    without it), so both are emitted together."""
    if stream:
        args = ["claude", "-p", "--output-format", "stream-json", "--verbose",
                "--settings", config.BRIDGE_SETTINGS_PATH,
                "--permission-mode", api_mode]
    else:
        args = ["claude", "-p", "--output-format", "json",
                "--settings", config.BRIDGE_SETTINGS_PATH,
                "--permission-mode", api_mode]
    if session_id:
        args += ["--resume", session_id]
    if system_prompt_file:
        args += ["--append-system-prompt-file", system_prompt_file]
    if approver_mcp_config:
        args += ["--permission-prompt-tool", "mcp__approver__approve",
                 "--mcp-config", approver_mcp_config, "--strict-mcp-config"]
    return args


# ── Settings canary (OV1) ─────────────────────────────────────────────────
# claude SILENTLY IGNORES a --settings file that fails validation. The canary proves
# deny is actually in force by attempting a must-be-denied action; if deny does not
# fire, the bot fails closed.
_CANARY_PROMPT = ("Run the shell command: printenv  — then stop. "
                  "Report only whether it ran or was blocked.")


def canary_passed(result_json: dict) -> bool:
    """True iff the must-be-denied canary action was actually denied. A Bash deny
    appears in permission_denials; an empty list means the deny did NOT fire → fail closed."""
    denials = result_json.get("permission_denials") or []
    return any(d.get("tool_name") == "Bash" for d in denials)


CANARY_OK = "ok"                      # claude ran AND the denied action was denied
CANARY_DENY_DROPPED = "deny_dropped"  # claude ran but deny did NOT fire → settings dropped
CANARY_CANNOT_RUN = "cannot_run"      # claude errored / not logged in / unparseable


def classify_canary(rc: "int | None", stdout: bytes) -> str:
    """Pure decision: map a canary subprocess (rc, stdout) to a CANARY_* status.

    A non-zero rc, an unparseable body, or an `is_error` result all mean claude never
    reached the tool-permission layer — CANNOT_RUN (retryable), NOT a deny-dropped
    security failure."""
    if rc is None or rc != 0:
        return CANARY_CANNOT_RUN
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, TypeError, ValueError):
        return CANARY_CANNOT_RUN
    if data.get("is_error"):
        return CANARY_CANNOT_RUN
    return CANARY_OK if canary_passed(data) else CANARY_DENY_DROPPED


async def _run_claude_subprocess(
    args: list[str], env: dict, *, cwd: str, prompt: str, timeout: int,
) -> tuple[int | None, bytes, bytes]:
    """The SINGLE place a `claude -p` subprocess is launched. Performs NO argument
    construction — `args` come from build_claude_args — and the prompt is always passed
    via stdin. Returns (returncode, stdout, stderr); returncode is None on timeout."""
    proc = await asyncio.create_subprocess_exec(
        *args, env=env, cwd=cwd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=prompt.encode()), timeout=timeout)
    except asyncio.TimeoutError:
        log.error("claude subprocess timeout — killing pid %s", proc.pid)
        proc.kill()
        try:
            await proc.communicate()
        except Exception:
            pass
        return (None, b"", b"")
    return (proc.returncode, stdout, stderr)


async def run_settings_canary(bot_name: str = "B") -> str:
    """Live pre-flight: run a tiny `claude -p` that attempts a denied command and
    classify the outcome (CANARY_OK / CANARY_DENY_DROPPED / CANARY_CANNOT_RUN)."""
    cfg = config.BOTS[bot_name]
    args = build_claude_args("default")
    env = build_subprocess_env(cfg)
    try:
        rc, stdout, _ = await _run_claude_subprocess(
            args, env, cwd=config.DEFAULT_CWD, prompt=_CANARY_PROMPT, timeout=config.CLAUDE_TIMEOUT)
    except OSError as e:
        log.error("settings canary could not run (%s)", e)
        return CANARY_CANNOT_RUN
    return classify_canary(rc, stdout)


# The permission/exec CHOKEPOINT (D3). Every agent invocation funnels through here;
# it is the only caller of build_claude_args + _run_claude_subprocess. Layers do NOT
# call it directly — they go through converse / execute below.
async def _call_claude(
    bot_name: str,
    prompt: str,
    *,
    mode: str,
    use_session: bool = True,
    system_prompt_file: "str | Path | None" = None,
    cwd: str | None = None,
) -> tuple[str, bool]:
    """Run `claude -p` for the given bot. Returns (reply_text, ok). The optional
    `system_prompt_file` is built by the caller (memory layer); the runner never
    reaches into memory itself."""
    cwd = config.DEFAULT_CWD if cwd is None else cwd
    cfg = config.BOTS[bot_name]
    api_mode = config.MODE_ALIASES.get(mode, mode)
    approver = mode == "approve"  # per-command MCP approval tier (runs in `default` mode)

    sid = sessions.load_session(bot_name, cwd) if use_session else None
    args = build_claude_args(
        api_mode, session_id=sid,
        system_prompt_file=str(system_prompt_file) if system_prompt_file else None,
        approver_mcp_config=config.APPROVER_MCP_CONFIG_PATH if approver else None)

    env = build_subprocess_env(cfg)
    if approver:
        env["APPROVER_SOCKET"] = config.APPROVER_SOCKET_PATH
    log.info("[%s] call mode=%s session=%s cwd=%s prompt_len=%d",
             bot_name, api_mode, use_session, cwd, len(prompt))

    call_timeout = config.approve_call_timeout() if approver else config.CLAUDE_TIMEOUT

    # Serialize calls sharing the same cwd (A/B same project → no concurrent writes)
    async with state.cwd_locks[cwd]:
        rc, stdout, stderr = await _run_claude_subprocess(
            args, env, cwd=cwd, prompt=prompt, timeout=call_timeout)

    if rc is None:
        return (f"⏱️ 響應超時（{call_timeout}s）", False)
    if rc != 0:
        err = stderr.decode("utf-8", errors="replace")[:500]
        log.error("[%s] exit=%d stderr=%s", bot_name, rc, err)
        return (f"❌ Claude 呼叫失敗 (exit {rc})：```{err}```", False)

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        out = stdout.decode("utf-8", errors="replace")[:500]
        return (f"❌ 解析失敗：```{out}```", False)

    reply = data.get("result") or "(空回覆)"
    new_sid = data.get("session_id")
    if new_sid and use_session:
        sessions.save_session(bot_name, new_sid, cwd)
    # record real context size so token-based flush can fire. Use the LAST iteration's
    # tokens, NOT the top-level usage (agentic calls sum tokens across iterations).
    if use_session:
        u = data.get("usage") or {}
        iters = u.get("iterations")
        src = iters[-1] if iters else u
        ctx = (src.get("input_tokens", 0) + src.get("cache_read_input_tokens", 0)
               + src.get("cache_creation_input_tokens", 0))
        if ctx:
            state.session_ctx_tokens[(bot_name, cwd)] = ctx
            log.info("[%s] context now ~%dk tokens (cwd=%s)", bot_name, ctx // 1000, cwd)
    return (reply, True)


# ── Two trust layers — the only public entries to the chokepoint (D3) ────────
_EXEC_MODES = {"edit", "acceptEdits", "bypass", "bypassPermissions", "approve"}


async def converse(
    bot_name: str,
    prompt: str,
    *,
    use_session: bool = True,
    system_prompt_file: "str | Path | None" = None,
    cwd: str | None = None,
) -> tuple[str, bool]:
    """Conversation-layer entry (A↔B debate, summaries, memory). HARD-CODES the
    read/plan mode; has no `mode` parameter, so it cannot escalate for any input."""
    return await _call_claude(
        bot_name, prompt, mode="plan", use_session=use_session,
        system_prompt_file=system_prompt_file, cwd=cwd,
    )


def exec_layer_for(is_bot_msg: bool, effective_mode: str) -> str:
    """Routing decision (D3 layer split): 'execute' ONLY for a human-driven
    execution-tier request (`edit`, or the M4 `approve` tier); every other case
    routes to 'converse'. Pure, so the structural guarantee is unit-testable."""
    if not is_bot_msg and effective_mode in ("edit", "approve"):
        return "execute"
    return "converse"


async def execute(
    bot_name: str,
    prompt: str,
    *,
    mode: str,
    use_session: bool = True,
    system_prompt_file: "str | Path | None" = None,
    cwd: str | None = None,
) -> tuple[str, bool]:
    """Execution-layer entry (human-driven). The ONLY path that may request a
    write/execute tier. Refuses any non-execution mode."""
    if mode not in _EXEC_MODES:
        raise ValueError(f"execute() requires an execution mode, got {mode!r}")
    return await _call_claude(
        bot_name, prompt, mode=mode, use_session=use_session,
        system_prompt_file=system_prompt_file, cwd=cwd,
    )


# ── Streaming execution (agent-exec-loop M1) ─────────────────────────────────
# Background exec jobs use the JSONL event stream so the operator watches progress.
# The parsing helpers below are pure (unit-tested); run_streaming_exec is the single
# streaming launcher, and reuses the same chokepoint discipline: argv via
# build_claude_args, prompt via stdin, env via build_subprocess_env.

# Exec-job outcomes returned by run_streaming_exec.
EXEC_DONE = "done"
EXEC_TIMEOUT = "timeout"
EXEC_FAILED = "failed"


def parse_stream_event(line: "bytes | str") -> "dict | None":
    """Tolerantly parse one JSONL line from `--output-format stream-json`. A blank line
    or an unparseable / non-object line returns None (tolerate parser drift across Claude
    Code upgrades — ignore, never crash)."""
    if isinstance(line, bytes):
        line = line.decode("utf-8", "replace")
    line = line.strip()
    if not line:
        return None
    try:
        ev = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    return ev if isinstance(ev, dict) else None


def is_result_event(event: dict) -> bool:
    """True for the terminal `result` event (carries reply text + session_id + usage)."""
    return isinstance(event, dict) and event.get("type") == "result"


def stream_trace_line(event: dict) -> "str | None":
    """A compact one-line trace for a tool-use action in an assistant event, else None.
    e.g. "Edit: bot.py", "Bash: pytest -q". Used for the rolling status trace."""
    if not isinstance(event, dict) or event.get("type") != "assistant":
        return None
    msg = event.get("message") or {}
    for item in (msg.get("content") or []):
        if not isinstance(item, dict) or item.get("type") != "tool_use":
            continue
        name = item.get("name", "?")
        inp = item.get("input") or {}
        detail = ""
        if name == "Bash":
            detail = (inp.get("command", "") or "").strip().splitlines()[0][:60] if inp.get("command") else ""
        elif name in ("Edit", "Write", "Read", "NotebookEdit"):
            detail = inp.get("file_path", "") or inp.get("notebook_path", "")
        elif name in ("Grep", "Glob"):
            detail = inp.get("pattern", "")
        return f"{name}: {detail}" if detail else name
    return None


def stream_ctx_tokens(usage: dict) -> int:
    """Last-iteration context size from a result event's usage (same accounting as the
    json path: sum input + cache-read + cache-creation of the final iteration)."""
    if not isinstance(usage, dict):
        return 0
    iters = usage.get("iterations")
    src = iters[-1] if iters else usage
    return (src.get("input_tokens", 0) + src.get("cache_read_input_tokens", 0)
            + src.get("cache_creation_input_tokens", 0))


async def kill_process_group(proc: "asyncio.subprocess.Process", grace: float = 5.0) -> None:
    """SIGTERM the process GROUP (grandchildren die too — spawned with start_new_session),
    wait a grace period, then SIGKILL, then reap. Safe to call on an already-dead proc."""
    if proc.returncode is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, PermissionError):
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace)
        return
    except asyncio.TimeoutError:
        pass
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        await proc.wait()
    except Exception:
        pass


# stdout StreamReader buffer: a single tool_use / tool_result JSONL line can be large
# (the agent writing a whole file). asyncio's 64 KB default would raise on such a line
# and orphan the subprocess; give it real headroom.
_STREAM_LIMIT = 16 * 1024 * 1024


async def run_streaming_exec(
    bot_name: str,
    prompt: str,
    *,
    mode: str,
    cwd: str,
    system_prompt_file: "str | Path | None" = None,
    on_trace: "Callable[[str], None] | None" = None,
    on_proc: "Callable[[asyncio.subprocess.Process], None] | None" = None,
    should_abort: "Callable[[], bool] | None" = None,
    timeout: "int | None" = None,
) -> tuple["str | None", str]:
    """Run an execution-tier task as a streaming subprocess. Returns (reply, outcome)
    where outcome is EXEC_DONE / EXEC_TIMEOUT / EXEC_FAILED.

    `on_trace(line)` is called (sync, best-effort) for each tool-use action for the live
    status trace; `on_proc(proc)` is called once the subprocess exists so the caller can
    register it for cancellation. `should_abort()` is checked right after spawn — if it
    returns True (a cancel landed while we were blocked on cwd_locks, before the proc
    existed to kill) the just-spawned process group is killed immediately. The subprocess
    is spawned in its own session so a cancel or timeout kills the whole group. Serialized
    per-cwd (never per-bot) so a long job does not stall conversation calls elsewhere."""
    if mode not in _EXEC_MODES:
        raise ValueError(f"run_streaming_exec requires an execution mode, got {mode!r}")
    timeout = config.EXEC_TIMEOUT if timeout is None else timeout
    cfg = config.BOTS[bot_name]
    api_mode = config.MODE_ALIASES.get(mode, mode)
    sid = sessions.load_session(bot_name, cwd)
    args = build_claude_args(
        api_mode, session_id=sid,
        system_prompt_file=str(system_prompt_file) if system_prompt_file else None,
        stream=True)
    env = build_subprocess_env(cfg)
    log.info("[%s] exec-job start mode=%s cwd=%s prompt_len=%d", bot_name, api_mode, cwd, len(prompt))

    async with state.cwd_locks[cwd]:
        proc = await asyncio.create_subprocess_exec(
            *args, env=env, cwd=cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            limit=_STREAM_LIMIT,
        )
        if on_proc:
            on_proc(proc)
        # A cancel may have arrived while we were blocked on cwd_locks (proc did not yet
        # exist to kill). Honour it now, deterministically, before doing any work.
        if should_abort and should_abort():
            await kill_process_group(proc)
            return (None, EXEC_FAILED)
        try:
            proc.stdin.write(prompt.encode())
            await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass

        final: "dict | None" = None
        try:
            async with asyncio.timeout(timeout):
                async for raw in proc.stdout:
                    event = parse_stream_event(raw)
                    if event is None:
                        continue
                    if is_result_event(event):
                        final = event
                        break  # result is terminal — don't wait on a lingering process
                    if on_trace:
                        line = stream_trace_line(event)
                        if line:
                            on_trace(line)
                # give a cleanly-finishing process a bounded moment to exit
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    await kill_process_group(proc)
        except asyncio.TimeoutError:
            log.error("[%s] exec-job timeout after %ds — killing process group", bot_name, timeout)
            await kill_process_group(proc)
            return (None, EXEC_TIMEOUT)
        except (ValueError, asyncio.LimitOverrunError):
            # an over-limit stdout line (shouldn't happen given _STREAM_LIMIT, but never
            # leave the subprocess running on the live tree if it does)
            log.error("[%s] exec-job stream read error — killing process group", bot_name)
            await kill_process_group(proc)
            return (None, EXEC_FAILED)

    if final is None:
        # cancelled (killed mid-stream) or the process died without a result event
        return (None, EXEC_FAILED)

    reply = final.get("result") or "(空回覆)"
    new_sid = final.get("session_id")
    if new_sid:
        sessions.save_session(bot_name, new_sid, cwd)
    ctx = stream_ctx_tokens(final.get("usage") or {})
    if ctx:
        state.session_ctx_tokens[(bot_name, cwd)] = ctx
        log.info("[%s] exec-job context now ~%dk tokens (cwd=%s)", bot_name, ctx // 1000, cwd)
    return (reply, EXEC_DONE)
