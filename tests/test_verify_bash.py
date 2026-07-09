"""agent-exec-loop M4 — post-task verification + exec-tier Bash (phase-2 gated).

Covers task 4.5: the tier is inert unless the phase-2 executor posture is proven; the
verify command is read from discord-state (never the worktree); the verify env carries
no Discord tokens / API keys; and the Bash-permitting exec settings still keep the whole
deny family (deny outranks allow). Plus the IPC verify round-trip and the settings pick.
"""
import asyncio
import json
import os
import textwrap
from pathlib import Path

import pytest

from bridge import config, runner, sessions


# ── 4.1 gate: inert unless opted-in AND phase-2 executor posture ─────────────
def test_m4_inert_when_flag_off(monkeypatch):
    monkeypatch.setattr(config, "EXEC_BASH_ENABLED", False)
    monkeypatch.setattr(config, "EXECUTOR_SOCKET", "/s/executor.sock")
    assert runner.m4_live() is False


def test_m4_inert_in_single_container_even_with_flag(monkeypatch):
    """Flag on but no split (no EXECUTOR_SOCKET) → the phase-2 posture is unproven, so
    the tier stays OFF. Fail-closed: shell/verify never run in the uncontained posture."""
    monkeypatch.setattr(config, "EXEC_BASH_ENABLED", True)
    monkeypatch.setattr(config, "EXECUTOR_SOCKET", None)
    assert runner.m4_live() is False


def test_m4_live_when_split_and_opted_in(monkeypatch):
    monkeypatch.setattr(config, "EXEC_BASH_ENABLED", True)
    monkeypatch.setattr(config, "EXECUTOR_SOCKET", "/s/executor.sock")
    assert runner.m4_live() is True


# ── 4.4 Bash allow keeps the deny family (deny outranks allow) ───────────────
def test_exec_settings_adds_bash_allow_preserving_deny(tmp_path, monkeypatch):
    base = tmp_path / "settings.json"
    deny = ["Read(//home/user/**/.credentials.json)", "Bash(curl)", "Bash(env)", "WebFetch"]
    base.write_text(json.dumps({"permissions": {"deny": deny}}))
    out = tmp_path / "exec-settings.json"
    monkeypatch.setattr(config, "BRIDGE_SETTINGS_PATH", str(base))
    monkeypatch.setattr(config, "EXEC_SETTINGS_PATH", str(out))
    runner.write_exec_settings()
    got = json.loads(out.read_text())
    assert "Bash" in got["permissions"]["allow"]        # exec tier may run Bash
    assert got["permissions"]["deny"] == deny           # every deny survives, unchanged
    # the specific-command denies (curl/env) still outrank the blanket Bash allow
    assert "Bash(curl)" in got["permissions"]["deny"]


def test_exec_settings_used_only_for_live_stream(tmp_path, monkeypatch):
    base = tmp_path / "settings.json"
    base.write_text(json.dumps({"permissions": {"deny": ["Bash(curl)"]}}))
    exec_path = tmp_path / "exec-settings.json"
    monkeypatch.setattr(config, "BRIDGE_SETTINGS_PATH", str(base))
    monkeypatch.setattr(config, "EXEC_SETTINGS_PATH", str(exec_path))
    monkeypatch.setattr(config, "BOTS", {"A": {"config_dir": "/c", "api_key": None}})
    monkeypatch.setattr(config, "USE_API_KEY", False)
    monkeypatch.setattr(config, "EXEC_BASH_ENABLED", True)
    monkeypatch.setattr(config, "EXECUTOR_SOCKET", "/s/x.sock")

    def _settings_of(stream):
        req = {"bot": "A", "api_mode": "acceptEdits", "session_id": None,
               "system_prompt_file": None, "cwd": "/p", "prompt": "x",
               "timeout": 60, "approver": False, "stream": stream}
        args, _, _ = runner._exec_request_to_spawn(req)
        return args[args.index("--settings") + 1]

    assert _settings_of(True) == str(exec_path)   # live stream exec → Bash-allow
    # regenerated fresh each spawn (tamper can't persist) with Bash allowed + deny kept
    got = json.loads(exec_path.read_text())
    assert "Bash" in got["permissions"]["allow"] and "Bash(curl)" in got["permissions"]["deny"]
    assert _settings_of(False) == str(base)       # non-stream → base deny-only
    monkeypatch.setattr(config, "EXEC_BASH_ENABLED", False)
    assert _settings_of(True) == str(base)        # tier off → base even for stream


# ── 4.2 verify: config from discord-state, stripped env, own timeout ─────────
@pytest.fixture
def verify_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "state")
    # verify config lives in its OWN (read-only-in-prod) dir, not the rw discord-state.
    monkeypatch.setattr(config, "VERIFY_CONFIG_DIR", tmp_path / "verify-config")
    config.verify_dir().mkdir(parents=True)
    project = "/home/user/proj"
    # workdir must live under STATE_DIR/worktrees (the executor validator pins it there)
    workdir = config.STATE_DIR / "worktrees" / "home-user-proj" / "j1"
    workdir.mkdir(parents=True)
    return project, str(workdir)


def _write_verify(project, command):
    p = config.verify_command_path(sessions._cwd_slug(project))
    p.write_text(command)


def test_verify_absent_config_is_not_green(verify_env):
    project, workdir = verify_env
    configured, passed, tail = asyncio.run(runner.run_verify(project, workdir))
    assert configured is False and passed is False and tail == ""


def test_verify_passing_command(verify_env):
    project, workdir = verify_env
    _write_verify(project, "echo hello && true")
    configured, passed, tail = asyncio.run(runner.run_verify(project, workdir))
    assert configured is True and passed is True and "hello" in tail


def test_verify_failing_command(verify_env):
    project, workdir = verify_env
    _write_verify(project, "echo boom; exit 3")
    configured, passed, tail = asyncio.run(runner.run_verify(project, workdir))
    assert configured is True and passed is False and "boom" in tail


def test_verify_runs_in_the_worktree(verify_env):
    project, workdir = verify_env
    (Path(workdir) / "marker.txt").write_text("x")
    _write_verify(project, "test -f marker.txt")  # relative → proves cwd is the worktree
    _, passed, _ = asyncio.run(runner.run_verify(project, workdir))
    assert passed is True


def test_verify_command_never_read_from_worktree(verify_env):
    """A .bridge-verify planted IN the worktree must be ignored — only the read-only
    verify config dir is honored (the agent must not author its own verify)."""
    project, workdir = verify_env
    (Path(workdir) / ".bridge-verify").write_text("exit 0")
    # no verify-config written
    configured, passed, _ = asyncio.run(runner.run_verify(project, workdir))
    assert configured is False and passed is False


def test_verify_config_dir_is_not_the_rw_state_volume():
    """The verify config dir must NOT live under the rw discord-state volume — the
    Bash-enabled exec agent mounts that rw and could forge its own green there. It
    defaults under SHARED_DIR (mounted :ro in the executor per the example compose)."""
    assert config.STATE_DIR not in config.VERIFY_CONFIG_DIR.parents
    assert config.VERIFY_CONFIG_DIR != config.STATE_DIR


def test_verify_slug_uses_resolved_project(verify_env):
    """A non-canonical project path resolves to the same verify file (no silent
    'not configured' from a slug mismatch with the whitelist)."""
    project, workdir = verify_env
    _write_verify(project, "true")  # keyed by the canonical slug
    configured, passed, _ = asyncio.run(
        runner.run_verify(project + "/./", workdir))  # non-canonical
    assert configured is True and passed is True


def test_verify_timeout(verify_env, monkeypatch):
    project, workdir = verify_env
    monkeypatch.setattr(config, "VERIFY_TIMEOUT", 1)
    _write_verify(project, "sleep 5")
    configured, passed, tail = asyncio.run(runner.run_verify(project, workdir))
    assert configured is True and passed is False and "timed out" in tail


def test_verify_exposes_proc_for_disconnect_kill(verify_env):
    """run_verify hands its process to on_proc so the IPC handler's disconnect watchdog
    can kill it — without this a cancelled verify would run to VERIFY_TIMEOUT."""
    project, workdir = verify_env
    _write_verify(project, "true")
    seen = []
    asyncio.run(runner.run_verify(project, workdir, on_proc=seen.append))
    assert len(seen) == 1 and hasattr(seen[0], "returncode")


# ── 4.5 verify env carries no Discord tokens / API keys ──────────────────────
def test_verify_env_strips_secrets():
    hostile = {
        "PATH": "/usr/bin", "HOME": "/home/user",
        "DISCORD_BOT_A_TOKEN": "tok", "DISCORD_BOT_B_TOKEN": "tok2",
        "ANTHROPIC_API_KEY": "k", "ANTHROPIC_API_KEY_A": "kA", "ANTHROPIC_API_KEY_B": "kB",
        "ANTHROPIC_AUTH_TOKEN": "at", "ANTHROPIC_BASE_URL": "u",
    }
    env = runner.build_verify_env(base_env=hostile)
    assert env["PATH"] == "/usr/bin" and env["HOME"] == "/home/user"
    for leaked in ("DISCORD_BOT_A_TOKEN", "DISCORD_BOT_B_TOKEN", "ANTHROPIC_API_KEY",
                   "ANTHROPIC_API_KEY_A", "ANTHROPIC_API_KEY_B", "ANTHROPIC_AUTH_TOKEN"):
        assert leaked not in env


def test_verify_subprocess_cannot_see_a_token(verify_env, monkeypatch):
    """End-to-end: a token in the executor's own env is not visible to the verify command."""
    project, workdir = verify_env
    monkeypatch.setenv("DISCORD_BOT_A_TOKEN", "SECRET-DISCORD")
    monkeypatch.setenv("ANTHROPIC_API_KEY_A", "SECRET-KEY")
    _write_verify(project, "echo TOKEN=[$DISCORD_BOT_A_TOKEN] KEY=[$ANTHROPIC_API_KEY_A]")
    _, _, tail = asyncio.run(runner.run_verify(project, workdir))
    assert "SECRET-DISCORD" not in tail and "SECRET-KEY" not in tail
    assert "TOKEN=[]" in tail and "KEY=[]" in tail


# ── IPC verify round-trip + inert-when-not-live ──────────────────────────────
@pytest.fixture
def verify_server_env(verify_env, tmp_path, monkeypatch):
    project, workdir = verify_env
    sock = str(tmp_path / "executor.sock")
    monkeypatch.setattr(config, "EXECUTOR_SOCKET", sock)
    monkeypatch.setattr(config, "PROJECT_DIRS", [Path(project)])
    return project, workdir


async def _with_server(coro):
    server = await asyncio.start_unix_server(
        runner._handle_executor_conn, path=config.EXECUTOR_SOCKET, limit=runner._STREAM_LIMIT)
    try:
        return await coro
    finally:
        server.close()
        await server.wait_closed()


def test_verify_ipc_round_trip_when_live(verify_server_env, monkeypatch):
    project, workdir = verify_server_env
    monkeypatch.setattr(config, "EXEC_BASH_ENABLED", True)  # m4_live() True (socket set)
    _write_verify(project, "echo ok && true")
    configured, passed, tail = asyncio.run(
        _with_server(runner.request_verify(project, workdir)))
    assert configured is True and passed is True and "ok" in tail


def test_verify_ipc_inert_when_tier_off(verify_server_env, monkeypatch):
    """Even with a config present, a non-live tier reports 'not configured' — never runs."""
    project, workdir = verify_server_env
    monkeypatch.setattr(config, "EXEC_BASH_ENABLED", False)
    _write_verify(project, "echo SHOULD-NOT-RUN")
    configured, passed, tail = asyncio.run(
        _with_server(runner.request_verify(project, workdir)))
    assert configured is False and passed is False and "SHOULD-NOT-RUN" not in tail


def test_verify_ipc_rejects_workdir_outside_worktree(verify_server_env, monkeypatch):
    project, _ = verify_server_env
    monkeypatch.setattr(config, "EXEC_BASH_ENABLED", True)
    configured, passed, tail = asyncio.run(
        _with_server(runner.request_verify(project, "/etc")))
    assert configured is False and "refused" in tail
