#!/usr/bin/env python3
"""MCP permission-prompt-tool server (M4). Spawned by `claude -p` via
`--permission-prompt-tool mcp__approver__approve --mcp-config …`. For each tool call
claude wants to make, it auto-allows known-safe ones (approver_policy) and escalates the
rest to the bridge over a unix socket (path in env APPROVER_SOCKET), which posts the
command to Discord and waits for a human ✅/❌.

Fail-closed everywhere: no socket, connection error, timeout, or malformed reply → DENY.
Minimal newline-delimited JSON-RPC; no third-party deps (claude spawns this per call).

Verified contract (claude 2.1.181, see openspec/.../m4-approver-preflight.md):
  request : tools/call {name:"approve", arguments:{tool_name, input:{command,...}, tool_use_id}}
  response: result {content:[{type:"text", text:'{"behavior":"allow","updatedInput":{…}}'
                                              | '{"behavior":"deny","message":"…"}'}]}
Only consulted under --permission-mode default (acceptEdits/bypass auto-accept and skip it).
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import approver_policy  # noqa: E402

ALLOWLIST_PATH = os.environ.get(
    "APPROVER_ALLOWLIST", str(Path(__file__).resolve().parent / "approver-allowlist.json"))
SOCKET_TIMEOUT = float(os.environ.get("APPROVER_SOCKET_TIMEOUT", "330"))

_ALLOWLIST = approver_policy.load_allowlist(ALLOWLIST_PATH)

_TOOL = {
    "name": "approve",
    "description": "Permission approver: auto-allows allow-listed tool calls, escalates the "
                   "rest to a human on Discord. Denies on timeout / error (fail-closed).",
    "inputSchema": {
        "type": "object",
        "properties": {"tool_name": {"type": "string"}, "input": {"type": "object"}},
    },
}


def _escalate_to_bridge(tool_name: str, command: str) -> tuple[bool, str]:
    """Ask the bridge (over the unix socket) for a human decision. Returns (allowed, reason).
    ANY failure → (False, ...) so a missing/broken approval channel never grants."""
    sock_path = os.environ.get("APPROVER_SOCKET")
    if not sock_path:
        return False, "no approval channel configured (fail-closed)"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(SOCKET_TIMEOUT)
            s.connect(sock_path)
            s.sendall((json.dumps({"tool_name": tool_name, "command": command}) + "\n").encode())
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
        resp = json.loads(buf.decode("utf-8", "replace").strip())
        return bool(resp.get("allowed")), str(resp.get("reason") or "")
    except Exception as e:  # connection refused, timeout, malformed — all fail closed
        return False, f"approval unavailable ({type(e).__name__}) — denied"


def evaluate(arguments: dict) -> dict:
    """Map a tools/call's arguments to a permission-prompt-tool decision payload."""
    tool_name = arguments.get("tool_name", "")
    tool_input = arguments.get("input") or {}
    verdict = approver_policy.decide(tool_name, tool_input, _ALLOWLIST)
    if verdict == "allow":
        return {"behavior": "allow", "updatedInput": tool_input}
    allowed, reason = _escalate_to_bridge(tool_name, tool_input.get("command", ""))
    if allowed:
        return {"behavior": "allow", "updatedInput": tool_input}
    return {"behavior": "deny", "message": reason or "denied by approver"}


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, mid = msg.get("method"), msg.get("id")
        if method == "initialize":
            pv = (msg.get("params") or {}).get("protocolVersion", "2025-06-18")
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": pv,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "bridge-approver", "version": "1.0.0"}}})
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [_TOOL]}})
        elif method == "tools/call":
            params = msg.get("params") or {}
            payload = evaluate(params.get("arguments") or {})
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": json.dumps(payload)}]}})
        elif mid is not None:
            _send({"jsonrpc": "2.0", "id": mid,
                   "error": {"code": -32601, "message": f"method {method} not found"}})


if __name__ == "__main__":
    main()
