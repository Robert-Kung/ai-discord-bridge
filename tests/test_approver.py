"""M4 — per-command MCP approver (spec: execution-permissions).

Covers the pure policy (auto-allow vs escalate, compound-command safety), the MCP server's
decision payloads incl. fail-closed escalation, the full JSON-RPC protocol over a real
subprocess, and the bot.py wiring (approve tier flags, default-closed gating, routing).
"""
import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import bot
import approver_policy
import mcp_approver

REPO = Path(bot.__file__).resolve().parent
ALLOWLIST = approver_policy.load_allowlist(REPO / "approver-allowlist.json")


# ── policy: auto-allow vs escalate ──────────────────────────────────────────
def test_auto_allow_tools_allow():
    for t in ("Read", "Edit", "Write", "Grep"):
        assert approver_policy.decide(t, {}, ALLOWLIST) == "allow"


def test_allowlisted_bash_allows():
    for cmd in ("pytest", "pytest tests/ -q", "git status --short", "npm run build"):
        assert approver_policy.decide("Bash", {"command": cmd}, ALLOWLIST) == "allow", cmd


def test_non_allowlisted_bash_escalates():
    for cmd in ("git push", "curl http://evil", "whoami", "rm -rf /", "git commit -m x"):
        assert approver_policy.decide("Bash", {"command": cmd}, ALLOWLIST) == "escalate", cmd


def test_compound_with_one_bad_segment_escalates():
    # an allow-listed segment must not green-light a piggy-backed non-listed one
    assert approver_policy.decide("Bash", {"command": "pytest && curl http://evil"}, ALLOWLIST) == "escalate"
    assert approver_policy.decide("Bash", {"command": "git status; rm -rf /"}, ALLOWLIST) == "escalate"


def test_compound_all_allowlisted_allows():
    assert approver_policy.decide("Bash", {"command": "git status && git diff"}, ALLOWLIST) == "allow"


def test_prefix_is_not_substring_match():
    # "git statusfoo" must NOT match the "git status" prefix
    assert approver_policy.decide("Bash", {"command": "git statusfoo"}, ALLOWLIST) == "escalate"


def test_empty_command_escalates():
    assert approver_policy.decide("Bash", {"command": ""}, ALLOWLIST) == "escalate"
    assert approver_policy.decide("Bash", {}, ALLOWLIST) == "escalate"


# ── MCP server evaluate(): decision payloads + fail-closed escalation ────────
def test_evaluate_auto_allows_without_socket(monkeypatch):
    monkeypatch.delenv("APPROVER_SOCKET", raising=False)
    out = mcp_approver.evaluate({"tool_name": "Bash", "input": {"command": "pytest"}})
    assert out["behavior"] == "allow"


def test_evaluate_escalation_denies_when_no_socket(monkeypatch):
    monkeypatch.delenv("APPROVER_SOCKET", raising=False)
    out = mcp_approver.evaluate({"tool_name": "Bash", "input": {"command": "git push"}})
    assert out["behavior"] == "deny"  # fail-closed: no approval channel → deny


class _FakeApprovalSocket:
    """A throwaway unix-socket server that returns a canned approval response."""
    def __init__(self, allowed, tmp_path, drop=False):
        self.allowed, self.drop = allowed, drop
        self.path = str(tmp_path / "approve.sock")
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        self.srv.listen(1)
        self.t = threading.Thread(target=self._serve, daemon=True)

    def _serve(self):
        conn, _ = self.srv.accept()
        with conn:
            conn.recv(65536)  # the request line
            if self.drop:
                return  # close without replying → client must fail closed
            conn.sendall((json.dumps({"allowed": self.allowed, "reason": "test"}) + "\n").encode())

    def __enter__(self):
        self.t.start()
        return self

    def __exit__(self, *a):
        self.srv.close()


def test_evaluate_escalation_allows_on_human_yes(monkeypatch, tmp_path):
    with _FakeApprovalSocket(True, tmp_path) as s:
        monkeypatch.setenv("APPROVER_SOCKET", s.path)
        out = mcp_approver.evaluate({"tool_name": "Bash", "input": {"command": "git push"}})
    assert out["behavior"] == "allow"


def test_evaluate_escalation_denies_on_human_no(monkeypatch, tmp_path):
    with _FakeApprovalSocket(False, tmp_path) as s:
        monkeypatch.setenv("APPROVER_SOCKET", s.path)
        out = mcp_approver.evaluate({"tool_name": "Bash", "input": {"command": "git push"}})
    assert out["behavior"] == "deny"


def test_evaluate_escalation_denies_on_socket_drop(monkeypatch, tmp_path):
    # server accepts then closes without replying → malformed/empty → fail closed
    with _FakeApprovalSocket(True, tmp_path, drop=True) as s:
        monkeypatch.setenv("APPROVER_SOCKET", s.path)
        out = mcp_approver.evaluate({"tool_name": "Bash", "input": {"command": "git push"}})
    assert out["behavior"] == "deny"


# ── full MCP JSON-RPC protocol over a real subprocess ───────────────────────
def test_mcp_protocol_end_to_end(monkeypatch):
    env = {**os.environ}
    env.pop("APPROVER_SOCKET", None)  # so an escalation denies deterministically
    proc = subprocess.Popen(
        [sys.executable, str(REPO / "mcp_approver.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env, text=True)
    msgs = [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "approve", "arguments": {"tool_name": "Bash", "input": {"command": "pytest"}}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "approve", "arguments": {"tool_name": "Bash", "input": {"command": "git push"}}}},
    ]
    out, _ = proc.communicate("\n".join(json.dumps(m) for m in msgs) + "\n", timeout=20)
    lines = [json.loads(ln) for ln in out.strip().splitlines() if ln.strip()]
    by_id = {m.get("id"): m for m in lines}
    assert by_id[1]["result"]["tools"][0]["name"] == "approve"
    allow_payload = json.loads(by_id[2]["result"]["content"][0]["text"])
    deny_payload = json.loads(by_id[3]["result"]["content"][0]["text"])
    assert allow_payload["behavior"] == "allow"   # allow-listed pytest
    assert deny_payload["behavior"] == "deny"     # escalation with no socket → fail closed


# ── bot.py wiring: approve tier flags, default-closed, routing ──────────────
def test_build_args_wires_approver_when_configured():
    args = bot.build_claude_args("default", approver_mcp_config="/tmp/x.json")
    assert "--permission-prompt-tool" in args
    assert args[args.index("--permission-prompt-tool") + 1] == "mcp__approver__approve"
    assert args[args.index("--mcp-config") + 1] == "/tmp/x.json"
    assert "--strict-mcp-config" in args
    # deny family still applies in the approver tier
    assert "--settings" in args


def test_build_args_no_approver_flags_by_default():
    args = bot.build_claude_args("acceptEdits")
    assert "--permission-prompt-tool" not in args and "--mcp-config" not in args
    assert "--settings" in args  # 5.3: with approver off, deny/sandbox still apply


def test_approve_mode_maps_to_default():
    assert bot.MODE_ALIASES["approve"] == "default"  # only mode the prompt-tool is consulted in


def test_approve_tier_default_closed(monkeypatch):
    monkeypatch.setattr(bot, "ALLOWED_USER_IDS", {111})
    monkeypatch.setattr(bot, "APPROVER_TIER_ENABLED", False)
    assert bot.approve_allowed(111) is False  # whitelisted but tier off → no


def test_approve_tier_requires_tier_and_whitelist(monkeypatch):
    monkeypatch.setattr(bot, "ALLOWED_USER_IDS", {111})
    monkeypatch.setattr(bot, "APPROVER_TIER_ENABLED", True)
    assert bot.approve_allowed(111) is True
    assert bot.approve_allowed(999) is False


def test_routing_human_approve_executes_bot_converses():
    assert bot.exec_layer_for(is_bot_msg=False, effective_mode="approve") == "execute"
    assert bot.exec_layer_for(is_bot_msg=True, effective_mode="approve") == "converse"


def test_execute_accepts_approve_mode():
    assert "approve" in bot._EXEC_MODES
