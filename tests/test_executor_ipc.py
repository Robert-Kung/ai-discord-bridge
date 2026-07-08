"""egress-exec-isolation 5.x — split-deploy executor IPC + per-container canary logic.

Covers: the executor's request validator (the frontend trust boundary — no argv/env
crosses the socket, only whitelisted parameter combos); real unix-socket round-trips
for both the json path (converse) and the streaming path (run_streaming_exec) against
a fake `claude`; the disconnect-kill contract (cancelling the remote handle kills the
executor-side process group); and the 5.3 canary classification (a tunnelable peer
host is a hard failure).
"""
import asyncio
import json
import os
import textwrap
from pathlib import Path

import pytest

from bridge import config, egress, runner, sessions, state


# ── 5.3 canary classification ────────────────────────────────────────────────
DENY, TUN, INC = egress.PROXY_DENIED, egress.PROXY_TUNNELED, egress.PROXY_INCONCLUSIVE


def test_forbidden_host_tunneled_is_hard_failure():
    assert egress.classify_egress(False, DENY, TUN, (TUN,)) == egress.EGRESS_PEER_REACHABLE
    # even outranks a down required host — posture beats reachability
    assert egress.classify_egress(False, DENY, DENY, (DENY, TUN)) == egress.EGRESS_PEER_REACHABLE


def test_forbidden_host_denied_is_ok():
    assert egress.classify_egress(False, DENY, TUN, (DENY, DENY)) == egress.EGRESS_OK


def test_forbidden_host_inconclusive_never_ok():
    assert egress.classify_egress(False, DENY, TUN, (INC,)) == egress.EGRESS_INCONCLUSIVE


def test_phase1_signature_unchanged():
    # existing three-arg calls (no forbidden set) classify exactly as before
    assert egress.classify_egress(False, DENY, TUN) == egress.EGRESS_OK
    assert egress.classify_egress(True, DENY, TUN) == egress.EGRESS_OPEN


# ── the split's secret-placement guarantees (5.1), from the reviewed template ─
import re as _re

_COMPOSE = Path(config.__file__).resolve().parent.parent / "docker-compose.example.yml"


def _service_block(name: str) -> str:
    lines = _COMPOSE.read_text().splitlines()
    start = lines.index(f"  {name}:")
    block = []
    for line in lines[start + 1:]:
        if line.strip() and _re.match(r"^(  \S|\S)", line):  # next service / top-level key
            break
        if line.strip().startswith("#"):  # prose must not trip the needle checks
            continue
        block.append(line)
    return "\n".join(block)


def test_frontend_container_has_no_claude_credentials():
    fe = _service_block("discord-frontend")
    for needle in (".credentials.json", ".claude-bot-", "settings.json",
                   "ANTHROPIC_API_KEY", "env_file"):
        assert needle not in fe, f"frontend must not carry {needle}"
    assert "EXECUTOR_SOCKET" in fe and "DISCORD_BOT_A_TOKEN" in fe


def test_executor_container_has_no_discord_material():
    ex = _service_block("executor")
    for needle in ("DISCORD_BOT", "DISCORD_CHANNEL_ID", "ALLOWED_USER_IDS", "env_file"):
        assert needle not in ex, f"executor must not carry {needle}"
    assert ".credentials.json" in ex and "EXECUTOR_SOCKET" in ex
    assert "proxy-anthropic" in ex  # its egress is the Anthropic-only proxy


# ── request validator (executor trust boundary) ──────────────────────────────
@pytest.fixture
def exec_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "state")
    (tmp_path / "state").mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setattr(config, "PROJECT_DIRS", [proj.resolve()])
    monkeypatch.setattr(config, "BOTS", {
        "A": {"token": None, "config_dir": str(tmp_path / "cfg-a"), "api_key": None},
        "B": {"token": None, "config_dir": str(tmp_path / "cfg-b"), "api_key": None},
    })
    return str(proj)


def _req(proj, **over):
    base = {"bot": "A", "api_mode": "plan", "session_id": None,
            "system_prompt_file": None, "cwd": proj, "prompt": "hi",
            "timeout": 60, "approver": False, "stream": False}
    base.update(over)
    return base


def test_validator_accepts_good_request(exec_config):
    assert runner._validate_exec_request(_req(exec_config)) is None


def test_validator_rejects_bad_fields(exec_config):
    proj = exec_config
    assert "bot" in runner._validate_exec_request(_req(proj, bot="C"))
    assert "api_mode" in runner._validate_exec_request(_req(proj, api_mode="bypassEverything"))
    assert "session_id" in runner._validate_exec_request(_req(proj, session_id="../../etc"))
    assert "cwd" in runner._validate_exec_request(_req(proj, cwd="/home/user/.claude-bot-a"))
    assert "timeout" in runner._validate_exec_request(_req(proj, timeout=10**9))
    assert runner._validate_exec_request(_req(proj, prompt=None)) is not None
    assert runner._validate_exec_request("nonsense") is not None


def test_validator_pins_system_prompt_to_state_dir(exec_config, tmp_path):
    proj = exec_config
    inside = config.STATE_DIR / "sysprompt" / "x.md"
    assert runner._validate_exec_request(_req(proj, system_prompt_file=str(inside))) is None
    outside = tmp_path / "cfg-a" / ".credentials.json"
    err = runner._validate_exec_request(_req(proj, system_prompt_file=str(outside)))
    assert err and "system_prompt_file" in err


def test_validator_accepts_worktree_cwd(exec_config):
    wt = config.STATE_DIR / "worktrees" / "proj" / "j1"
    assert runner._validate_exec_request(_req(exec_config, cwd=str(wt))) is None


# ── unix-socket round-trips against a fake claude ────────────────────────────
@pytest.fixture
def split_env(exec_config, tmp_path, monkeypatch):
    """Fake claude on PATH + EXECUTOR_SOCKET pointing at a tmp socket."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "claude").write_text(textwrap.dedent('''\
        #!/usr/bin/env python3
        import sys, json, os
        argv = sys.argv[1:]
        sys.stdin.read()
        if "stream-json" in argv:
            print(json.dumps({"type":"assistant","message":{"content":[
                {"type":"tool_use","name":"Edit","input":{"file_path":"a.txt"}}]}}), flush=True)
            print(json.dumps({"type":"result","result":"remote-edited",
                              "session_id":"s-remote-stream","usage":{"input_tokens":7}}), flush=True)
        else:
            print(json.dumps({"result":"remote-reply","session_id":"s-remote",
                              "usage":{"input_tokens":5}}))
    '''))
    os.chmod(bindir / "claude", 0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    sock = str(tmp_path / "state" / "executor.sock")
    monkeypatch.setattr(config, "EXECUTOR_SOCKET", sock)
    return exec_config


async def _with_server(coro):
    server = await asyncio.start_unix_server(
        runner._handle_executor_conn, path=config.EXECUTOR_SOCKET)
    try:
        return await coro
    finally:
        server.close()
        await server.wait_closed()


def test_remote_converse_round_trip(split_env):
    proj = split_env

    async def go():
        reply, ok = await runner.converse("A", "hello", use_session=True, cwd=proj)
        return reply, ok

    reply, ok = asyncio.run(_with_server(go()))
    assert ok and reply == "remote-reply"
    assert sessions.load_session("A", proj) == "s-remote"          # saved frontend-side
    assert state.session_ctx_tokens[("A", proj)] == 5


def test_remote_streaming_exec_round_trip(split_env):
    proj = split_env
    trace = []

    async def go():
        return await runner.run_streaming_exec(
            "A", "edit", mode="edit", cwd=proj, project=proj,
            on_trace=trace.append, on_proc=lambda p: None,
            should_abort=lambda: False, timeout=30)

    reply, outcome = asyncio.run(_with_server(go()))
    assert outcome == runner.EXEC_DONE and reply == "remote-edited"
    assert any("Edit: a.txt" in t for t in trace)
    assert sessions.load_session("A", proj) == "s-remote-stream"
    assert state.session_ctx_tokens[("A", proj)] == 7


def test_remote_rejection_surfaces_as_failure(split_env, tmp_path):
    async def go():
        return await runner.converse("A", "hello", cwd=str(tmp_path / "not-whitelisted"))

    reply, ok = asyncio.run(_with_server(go()))
    assert not ok and "executor refused" in reply


def test_executor_unreachable_fails_cleanly(split_env, monkeypatch):
    monkeypatch.setattr(config, "EXECUTOR_SOCKET", str(Path(split_env) / "nope.sock"))
    reply, ok = asyncio.run(runner.converse("A", "hello", cwd=split_env))
    assert not ok and "executor unreachable" in reply


def test_disconnect_kills_executor_side_process(split_env, tmp_path, monkeypatch):
    """The disconnect-kill contract: cancelling the remote handle (what jobs.cancel_job
    does via kill_process_group) must kill the claude process group in the executor."""
    proj = split_env
    pidfile = tmp_path / "child.pid"
    bindir = tmp_path / "bin"
    (bindir / "claude").write_text(textwrap.dedent(f'''\
        #!/usr/bin/env python3
        import os, sys, time
        open({str(pidfile)!r}, "w").write(str(os.getpid()))
        sys.stdout.write('{{"type":"assistant","message":{{"content":[]}}}}\\n')
        sys.stdout.flush()
        time.sleep(60)
    '''))
    os.chmod(bindir / "claude", 0o755)

    handles = []

    async def go():
        task = asyncio.ensure_future(runner.run_streaming_exec(
            "A", "spin", mode="edit", cwd=proj, project=proj,
            on_trace=lambda l: None, on_proc=handles.append,
            should_abort=lambda: False, timeout=50))
        for _ in range(100):                      # wait for the child to exist
            if pidfile.exists():
                break
            await asyncio.sleep(0.05)
        assert handles, "on_proc never delivered a handle"
        await runner.kill_process_group(handles[0])   # the jobs.cancel_job path
        reply, outcome = await task
        pid = int(pidfile.read_text())
        for _ in range(100):                      # executor must reap the group
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("executor-side claude survived the disconnect")
        return reply, outcome

    reply, outcome = asyncio.run(_with_server(go()))
    assert reply is None and outcome == runner.EXEC_FAILED
