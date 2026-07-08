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
import re
import signal
from pathlib import Path
from typing import Callable

from bridge import config, sessions, state

log = logging.getLogger("bridge.runner")


def build_subprocess_env(cfg: dict, base_env: "dict | None" = None) -> dict:
    """Env for a `claude -p` subprocess: strip the secret/auth-routing family and set
    this bot's CLAUDE_CONFIG_DIR. API-key mode no longer injects the key into the env —
    the CLI reads it via the per-bot `apiKeyHelper` provisioned in the config dir
    (see provision_api_key_helper), so `printenv` and env-dump vectors find nothing.
    Pure: pass base_env in tests instead of monkeypatching os.environ."""
    src = os.environ if base_env is None else base_env
    env = {k: v for k, v in src.items() if k not in config._SUBPROCESS_ENV_DENY}
    env["CLAUDE_CONFIG_DIR"] = cfg["config_dir"]
    # Egress containment (phase 1): route `claude`'s HTTPS through the allow-list proxy
    # and drop non-essential telemetry so those endpoints need not be opened. The proxy
    # env is plumbing, not the boundary — the boundary is the routeless internal network.
    if config.EGRESS_PROXY_URL:
        env["HTTPS_PROXY"] = config.EGRESS_PROXY_URL
        env["HTTP_PROXY"] = config.EGRESS_PROXY_URL
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    return env


def provision_api_key_helper(cfg: dict) -> None:
    """API-key mode ONLY (caller gates on config.USE_API_KEY): materialize this bot's
    key as a 0600 file in its config dir, an executable apiKeyHelper that emits it, and
    point the config-dir settings.json at the helper. Subscription mode never calls
    this, so the OAuth auth path is untouched by construction (the deferred-task
    precedence concern). Live-verified 2026-07-08: a config-dir settings.json
    apiKeyHelper IS consulted by `claude -p`. Fail-loud: a config dir we cannot
    provision means auth would resolve somewhere we did not choose."""
    d = Path(cfg["config_dir"])
    d.mkdir(parents=True, exist_ok=True)
    key = cfg.get("api_key")
    key_file = d / config.API_KEY_FILENAME
    if key:  # env-seeded: (re)write the key file from .env
        key_file.write_text(key + "\n")
        key_file.chmod(0o600)
    elif not key_file.exists():  # file-seeded: operator must have dropped it
        raise SystemExit(
            f"USE_API_KEY set but no key for this bot: neither an env key nor "
            f"{key_file} exists — refusing to start with unresolved auth.")
    helper = d / config.API_KEY_HELPER_FILENAME
    helper.write_text(f'#!/bin/sh\nexec cat "$(dirname "$0")/{config.API_KEY_FILENAME}"\n')
    helper.chmod(0o700)
    settings_path = d / "settings.json"
    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            raise SystemExit(
                f"cannot parse {settings_path} to install apiKeyHelper: {e} — "
                "refusing to guess (fix or remove the file).") from e
        if not isinstance(settings, dict):  # valid JSON but a list/scalar
            raise SystemExit(
                f"{settings_path} is not a JSON object (got {type(settings).__name__}) — "
                "cannot install apiKeyHelper; fix or remove the file.")
    settings["apiKeyHelper"] = str(helper)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    log.info("apiKeyHelper provisioned for config dir %s", d)


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
    classify the outcome (CANARY_OK / CANARY_DENY_DROPPED / CANARY_CANNOT_RUN).
    Spawns claude LOCALLY, so it must run where claude actually runs — the single
    container, or the EXECUTOR in a split deploy (never the credential-less frontend)."""
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


async def settings_canary_gate() -> None:
    """Fail-closed startup gate shared by bot.py (single container) and executor.py
    (split deploy). OK → return; DENY_DROPPED → SystemExit (config bug, retry won't fix);
    CANNOT_RUN → in-process backoff retry (auth lapse — a SystemExit here would hand
    docker a crash-loop, the canary_oauth_crashloop lesson)."""
    backoff = config.CANARY_RETRY_BASE
    while True:
        status = await run_settings_canary()
        if status == CANARY_OK:
            log.info("settings canary passed: --settings deny family is in force")
            return
        if status == CANARY_DENY_DROPPED:
            raise SystemExit(
                "settings canary failed — claude ran but the must-be-denied action was "
                "NOT denied: the --settings permissions.deny family is not in force "
                "(schema drift / unloadable settings). Refusing to start. "
                "Set BRIDGE_SKIP_CANARY=1 only for offline dev.")
        log.error("settings canary could not run — claude is unavailable / not logged in. "
                  "Not serving until auth recovers; retrying in %ds (in-process, no restart "
                  "loop).", backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, config.CANARY_RETRY_MAX)


# ── Executor IPC (egress-exec-isolation 5.x) ─────────────────────────────────
# Split deploy: the frontend never spawns claude — it sends a SEMANTIC request over a
# unix socket and the executor (the only container with credentials) assembles argv/env
# and spawns. The request carries no argv and no env: a compromised frontend can only
# ask for parameter combinations the validator below accepts, never arbitrary exec.
# Both halves live in THIS module on purpose — the AST boundary test pins argv assembly
# and subprocess launch to the runner, and the IPC server is exactly those two things.
#
# Wire protocol (JSONL over the socket, one connection per call):
#   request  {"bot","api_mode","session_id","system_prompt_file","cwd","prompt",
#             "timeout","approver","stream"}
#   non-stream response  {"ok":true,"rc":int|null,"stdout":str,"stderr":str}
#                        | {"ok":false,"error":str}
#   stream response      {"ev":"line","data":str}* then {"ev":"exit","rc":int|null}
#                        | {"ev":"error","error":str}
# rc null = executor-side timeout (process group killed). Client disconnect (any
# reason, incl. client-side timeout or job cancel) → the executor kills the group.

_SESSION_ID_RE = re.compile(r"^[0-9a-zA-Z-]{8,64}$")


def _validate_exec_request(req: dict) -> "str | None":
    """Return an error string if the request is not a parameter combination we are
    willing to run, else None. This is the executor's trust boundary with the frontend."""
    if not isinstance(req, dict):
        return "request is not an object"
    if req.get("bot") not in config.BOTS:
        return f"unknown bot {req.get('bot')!r}"
    if req.get("api_mode") not in set(config.MODE_ALIASES.values()):
        return f"disallowed api_mode {req.get('api_mode')!r}"
    sid = req.get("session_id")
    if sid is not None and not (isinstance(sid, str) and _SESSION_ID_RE.match(sid)):
        return "malformed session_id"
    spf = req.get("system_prompt_file")
    if spf is not None:
        p = Path(str(spf)).resolve()
        if not str(p).startswith(str(config.STATE_DIR.resolve()) + os.sep):
            return "system_prompt_file outside STATE_DIR"
    cwd = req.get("cwd")
    if not isinstance(cwd, str):
        return "missing cwd"
    rcwd = Path(cwd).resolve()
    wt_root = (config.STATE_DIR / "worktrees").resolve()
    allowed = (rcwd == Path(config.DEFAULT_CWD).resolve()
               or any(rcwd == p or rcwd.is_relative_to(p) for p in config.PROJECT_DIRS)
               or rcwd.is_relative_to(wt_root))
    if not allowed:
        return f"cwd {cwd!r} not in the project/worktree whitelist"
    t = req.get("timeout")
    if not isinstance(t, (int, float)) or not (0 < t <= max(
            config.EXEC_TIMEOUT, config.approve_call_timeout()) + 60):
        return f"timeout {t!r} out of range"
    if not isinstance(req.get("prompt"), str):
        return "missing prompt"
    if not isinstance(req.get("approver"), bool) or not isinstance(req.get("stream"), bool):
        return "approver/stream must be booleans"
    return None


def _exec_request_to_spawn(req: dict) -> tuple[list, dict, str]:
    """Assemble (argv, env, cwd) for a VALIDATED request — executor side, so argv/env
    construction (and the credentials they imply) never exist in the frontend."""
    cfg = config.BOTS[req["bot"]]
    args = build_claude_args(
        req["api_mode"], session_id=req.get("session_id"),
        system_prompt_file=req.get("system_prompt_file"),
        approver_mcp_config=config.APPROVER_MCP_CONFIG_PATH if req["approver"] else None,
        stream=req["stream"])
    env = build_subprocess_env(cfg)
    if req["approver"]:
        env["APPROVER_SOCKET"] = config.APPROVER_SOCKET_PATH
    return args, env, req["cwd"]


async def _handle_executor_conn(reader: asyncio.StreamReader,
                                writer: asyncio.StreamWriter) -> None:
    """One connection = one claude run. Any validation/spawn error is reported and the
    connection closed; a client disconnect at any point kills the process group.

    Disconnect-kill via a single watchdog: `reader.read(1)` completes only on EOF (the
    client closed — cancel/crash/its own timeout) or a stray byte (a protocol-violating
    client), and either way we kill the process group. read(1) is bounded, so a hostile
    client that floods the socket can't grow executor memory (read(-1) would)."""
    is_stream = False

    def send(obj: dict) -> None:
        writer.write((json.dumps(obj) + "\n").encode())

    proc = None
    watcher = None
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=30)
        try:
            req = json.loads(line.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            req = None
        err = _validate_exec_request(req)
        if err:
            log.warning("executor: rejected request (%s)", err)
            send({"ev": "error", "error": err} if (isinstance(req, dict) and req.get("stream"))
                 else {"ok": False, "error": err})
            await writer.drain()
            return
        is_stream = req["stream"]
        args, env, cwd = _exec_request_to_spawn(req)
        timeout = float(req["timeout"])
        log.info("executor: run bot=%s mode=%s stream=%s cwd=%s prompt_len=%d",
                 req["bot"], req["api_mode"], is_stream, cwd, len(req["prompt"]))
        proc = await asyncio.create_subprocess_exec(
            *args, env=env, cwd=cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            limit=_STREAM_LIMIT,
        )

        async def _watch_disconnect(p):
            try:
                await reader.read(1)  # EOF (disconnect) or a stray byte
            except Exception:
                pass
            await kill_process_group(p)  # client gone → don't keep burning quota

        watcher = asyncio.ensure_future(_watch_disconnect(proc))

        try:
            if is_stream:
                try:
                    proc.stdin.write(req["prompt"].encode())
                    await proc.stdin.drain()
                    proc.stdin.close()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                try:
                    async with asyncio.timeout(timeout):
                        while True:
                            raw = await proc.stdout.readline()
                            if not raw:
                                break  # proc exited (EOF), incl. watchdog-killed on disconnect
                            send({"ev": "line", "data": raw.decode("utf-8", "replace")})
                            await writer.drain()
                        await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    await kill_process_group(proc)
                    send({"ev": "exit", "rc": None})
                    await writer.drain()
                    return
                send({"ev": "exit", "rc": proc.returncode})
                await writer.drain()
            else:
                rc, stdout, stderr = None, b"", b""
                try:
                    stdout, stderr = await asyncio.wait_for(
                        proc.communicate(input=req["prompt"].encode()), timeout=timeout)
                    rc = proc.returncode
                except asyncio.TimeoutError:
                    await kill_process_group(proc)
                send({"ok": True, "rc": rc,
                      "stdout": stdout.decode("utf-8", "replace"),
                      "stderr": stderr.decode("utf-8", "replace")})
                await writer.drain()
        finally:
            if watcher is not None:
                watcher.cancel()
    except (ConnectionResetError, BrokenPipeError):
        log.info("executor: client disconnected — killing the run")
    except Exception:
        log.exception("executor: connection handler failed")
        try:
            # match the in-flight request's wire shape so the client actually surfaces it
            send({"ev": "error", "error": "internal executor error"} if is_stream
                 else {"ok": False, "error": "internal executor error"})
            await writer.drain()
        except Exception:
            pass
    finally:
        if proc is not None:
            await kill_process_group(proc)
        try:
            writer.close()
        except Exception:
            pass


async def serve_executor() -> None:
    """Bind the executor unix socket and serve forever (executor.py entrypoint)."""
    sock = config.EXECUTOR_SOCKET
    if not sock:
        raise SystemExit("serve_executor requires EXECUTOR_SOCKET to be set")
    Path(sock).parent.mkdir(parents=True, exist_ok=True)
    try:
        os.unlink(sock)
    except FileNotFoundError:
        pass
    # limit=_STREAM_LIMIT: a request line carries the whole prompt, which can be large
    # (M3 attachments etc.); the asyncio default 64 KiB would raise on it.
    server = await asyncio.start_unix_server(
        _handle_executor_conn, path=sock, limit=_STREAM_LIMIT)
    os.chmod(sock, 0o600)
    log.info("executor serving on %s", sock)
    async with server:
        await server.serve_forever()


class RemoteExecHandle:
    """Cancellation handle for a remote streaming run: closing the IPC connection is
    the kill (the executor's disconnect-kill contract). Quacks enough like a Process
    for jobs.attach_proc/cancel_job."""
    def __init__(self, writer: asyncio.StreamWriter):
        self._writer = writer
        self.returncode: "int | None" = None
        self.pid = -1

    async def remote_kill(self) -> None:
        self.returncode = -15
        try:
            self._writer.close()
        except Exception:
            pass


def _build_remote_request(bot_name: str, prompt: str, *, api_mode: str, sid: "str | None",
                          system_prompt_file, cwd: str, timeout: float,
                          approver: bool, stream: bool) -> dict:
    return {"bot": bot_name, "api_mode": api_mode, "session_id": sid,
            "system_prompt_file": str(system_prompt_file) if system_prompt_file else None,
            "cwd": cwd, "prompt": prompt, "timeout": timeout,
            "approver": approver, "stream": stream}


async def _remote_run(req: dict) -> tuple["int | None", bytes, bytes]:
    """Non-stream remote call. Mirrors _run_claude_subprocess's return contract:
    (rc, stdout, stderr); rc None = timeout. IPC failure → rc 255 with the error in
    stderr (surfaces as a normal call failure, never crashes the caller)."""
    try:
        reader, writer = await asyncio.open_unix_connection(
            config.EXECUTOR_SOCKET, limit=_STREAM_LIMIT)
    except OSError as e:
        return (255, b"", f"executor unreachable: {e}".encode())
    try:
        writer.write((json.dumps(req) + "\n").encode())
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=req["timeout"] + 30)
        resp = json.loads(line.decode("utf-8", "replace"))
        if not resp.get("ok"):
            return (255, b"", f"executor refused: {resp.get('error')}".encode())
        return (resp["rc"], resp["stdout"].encode(), resp["stderr"].encode())
    # ValueError also covers LimitOverrunError (a response line past _STREAM_LIMIT);
    # degrade to the (255, …) contract rather than crash the caller.
    except (OSError, asyncio.TimeoutError, ValueError, KeyError) as e:
        return (255, b"", f"executor IPC failure: {e}".encode())
    finally:
        try:
            writer.close()
        except Exception:
            pass


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
    log.info("[%s] call mode=%s session=%s cwd=%s prompt_len=%d",
             bot_name, api_mode, use_session, cwd, len(prompt))

    call_timeout = config.approve_call_timeout() if approver else config.CLAUDE_TIMEOUT

    # Serialize calls sharing the same cwd (A/B same project → no concurrent writes)
    async with state.cwd_locks[cwd]:
        if config.EXECUTOR_SOCKET:
            # split deploy: semantic request over IPC — argv/env exist only executor-side
            rc, stdout, stderr = await _remote_run(_build_remote_request(
                bot_name, prompt, api_mode=api_mode, sid=sid,
                system_prompt_file=system_prompt_file, cwd=cwd,
                timeout=call_timeout, approver=approver, stream=False))
        else:
            args = build_claude_args(
                api_mode, session_id=sid,
                system_prompt_file=str(system_prompt_file) if system_prompt_file else None,
                approver_mcp_config=config.APPROVER_MCP_CONFIG_PATH if approver else None)
            env = build_subprocess_env(cfg)
            if approver:
                env["APPROVER_SOCKET"] = config.APPROVER_SOCKET_PATH
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


async def kill_process_group(proc, grace: float = 5.0) -> None:
    """SIGTERM the process GROUP (grandchildren die too — spawned with start_new_session),
    wait a grace period, then SIGKILL, then reap. Safe to call on an already-dead proc.
    A RemoteExecHandle (split deploy) is killed by closing its IPC connection instead —
    the executor's disconnect-kill contract does the group kill on its side."""
    if hasattr(proc, "remote_kill"):
        await proc.remote_kill()
        return
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
    project: "str | None" = None,
    system_prompt_file: "str | Path | None" = None,
    on_trace: "Callable[[str], None] | None" = None,
    on_proc: "Callable[[asyncio.subprocess.Process], None] | None" = None,
    should_abort: "Callable[[], bool] | None" = None,
    timeout: "int | None" = None,
) -> tuple["str | None", str]:
    """Run an execution-tier task as a streaming subprocess. Returns (reply, outcome)
    where outcome is EXEC_DONE / EXEC_TIMEOUT / EXEC_FAILED.

    PROJECT IDENTITY vs SUBPROCESS CWD (M2): `cwd` is where the subprocess runs (a git
    worktree for a diff-gated job); `project` is the stable project path that keys the
    session, the cwd lock, and the token accounting. They differ for a worktree job —
    otherwise every job would burn a fresh session, litter discord-state, and lose the
    project's summary/notes memory. `project` defaults to `cwd` (M1 / non-git dirs).

    `on_trace(line)` is called (sync, best-effort) for each tool-use action for the live
    status trace; `on_proc(proc)` is called once the subprocess exists so the caller can
    register it for cancellation. `should_abort()` is checked right after spawn — if it
    returns True (a cancel landed while we were blocked on the lock, before the proc
    existed to kill) the just-spawned process group is killed immediately. The subprocess
    is spawned in its own session so a cancel or timeout kills the whole group. Serialized
    per-project (never per-bot) so a long job does not stall conversation calls elsewhere."""
    if mode not in _EXEC_MODES:
        raise ValueError(f"run_streaming_exec requires an execution mode, got {mode!r}")
    timeout = config.EXEC_TIMEOUT if timeout is None else timeout
    project = cwd if project is None else project
    cfg = config.BOTS[bot_name]
    api_mode = config.MODE_ALIASES.get(mode, mode)
    sid = sessions.load_session(bot_name, project)
    log.info("[%s] exec-job start mode=%s cwd=%s project=%s prompt_len=%d",
             bot_name, api_mode, cwd, project, len(prompt))

    if config.EXECUTOR_SOCKET:
        async with state.cwd_locks[project]:
            final, outcome = await _remote_stream_exec(
                _build_remote_request(bot_name, prompt, api_mode=api_mode, sid=sid,
                                      system_prompt_file=system_prompt_file, cwd=cwd,
                                      timeout=timeout, approver=False, stream=True),
                on_trace=on_trace, on_proc=on_proc, should_abort=should_abort)
        if outcome is not None:
            return (None, outcome)
        return _finish_stream_result(final, bot_name, project)

    args = build_claude_args(
        api_mode, session_id=sid,
        system_prompt_file=str(system_prompt_file) if system_prompt_file else None,
        stream=True)
    env = build_subprocess_env(cfg)

    async with state.cwd_locks[project]:
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
    return _finish_stream_result(final, bot_name, project)


def _finish_stream_result(final: "dict | None", bot_name: str, project: str) -> tuple["str | None", str]:
    """Shared result-event bookkeeping for the local and remote streaming paths:
    session save + last-iteration token accounting keyed by PROJECT identity."""
    if final is None:
        return (None, EXEC_FAILED)
    reply = final.get("result") or "(空回覆)"
    new_sid = final.get("session_id")
    if new_sid:
        sessions.save_session(bot_name, new_sid, project)
    ctx = stream_ctx_tokens(final.get("usage") or {})
    if ctx:
        state.session_ctx_tokens[(bot_name, project)] = ctx
        log.info("[%s] exec-job context now ~%dk tokens (project=%s)", bot_name, ctx // 1000, project)
    return (reply, EXEC_DONE)


async def _remote_stream_exec(
    req: dict,
    *,
    on_trace: "Callable[[str], None] | None",
    on_proc: "Callable | None",
    should_abort: "Callable[[], bool] | None",
) -> tuple["dict | None", "str | None"]:
    """Streaming exec over the executor IPC. Returns (final_event, outcome_override):
    outcome_override is EXEC_TIMEOUT / EXEC_FAILED for early exits, or None meaning
    'use final_event' (which may itself be None → EXEC_FAILED upstream). Cancellation:
    the caller's kill path closes the connection (RemoteExecHandle), and the executor
    kills the process group on disconnect."""
    try:
        reader, writer = await asyncio.open_unix_connection(
            config.EXECUTOR_SOCKET, limit=_STREAM_LIMIT)
    except OSError as e:
        log.error("executor unreachable for streaming exec: %s", e)
        return (None, EXEC_FAILED)
    handle = RemoteExecHandle(writer)
    if on_proc:
        on_proc(handle)
    if should_abort and should_abort():
        await handle.remote_kill()
        return (None, EXEC_FAILED)
    final: "dict | None" = None
    try:
        writer.write((json.dumps(req) + "\n").encode())
        await writer.drain()
        # executor enforces the subprocess timeout; this outer margin only covers a
        # hung/dead executor, so the frontend can never wait forever on the socket.
        async with asyncio.timeout(req["timeout"] + 60):
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                try:
                    msg = json.loads(raw.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    continue
                if msg.get("ev") == "error":
                    log.error("executor refused streaming exec: %s", msg.get("error"))
                    return (None, EXEC_FAILED)
                if msg.get("ev") == "exit":
                    if msg.get("rc") is None and final is None:
                        return (None, EXEC_TIMEOUT)
                    break
                if msg.get("ev") == "line":
                    event = parse_stream_event(msg.get("data", ""))
                    if event is None:
                        continue
                    if is_result_event(event):
                        final = event
                    elif on_trace:
                        line = stream_trace_line(event)
                        if line:
                            on_trace(line)
    except asyncio.TimeoutError:
        log.error("executor IPC stalled past the exec timeout — abandoning the run")
        return (None, EXEC_TIMEOUT)
    # ValueError covers a stream line past _STREAM_LIMIT (LimitOverrunError).
    except (OSError, ConnectionResetError, ValueError):
        return (None, EXEC_FAILED)
    finally:
        try:
            writer.close()
        except Exception:
            pass
    return (final, None)
