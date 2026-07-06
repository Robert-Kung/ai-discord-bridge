"""M4 per-command approver: the unix-socket server a spawned mcp_approver.py connects
to. The human round-trip itself is frontend turf (it owns pending_actions + the Discord
client), so it is injected as an `ask_human(command, tool_name) -> bool` callable at
server start; this module never imports the frontend.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Awaitable, Callable

from bridge import config

log = logging.getLogger("bridge.approver_ipc")

# Set by start_approval_server(); the frontend supplies request_discord_approval.
_ask_human: "Callable[[str, str], Awaitable[bool]] | None" = None


async def _handle_approval_request(reader: asyncio.StreamReader,
                                   writer: asyncio.StreamWriter) -> None:
    """Unix-socket handler: a spawned mcp_approver.py asks us to approve one command.
    Any error → deny (fail-closed)."""
    allowed = False
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=10)
        req = json.loads(line.decode("utf-8", "replace"))
        if _ask_human is None:
            raise RuntimeError("approval server started without an ask_human callable")
        allowed = await _ask_human(req.get("command", ""), req.get("tool_name", "Bash"))
    except Exception as e:
        log.warning("approval request failed (%s) — denying", e)
        allowed = False
    try:
        writer.write((json.dumps({"allowed": allowed}) + "\n").encode())
        await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()


async def start_approval_server(ask_human: "Callable[[str, str], Awaitable[bool]]") -> None:
    """Bind the approval unix socket (M4) and record the human-approval callable. Also
    (re)writes the --mcp-config the chokepoint points claude at."""
    global _ask_human
    _ask_human = ask_human
    cfg = {"mcpServers": {"approver": {
        "command": sys.executable,
        "args": [config.APPROVER_SCRIPT],
        "env": {"APPROVER_SOCKET": config.APPROVER_SOCKET_PATH,
                "APPROVER_ALLOWLIST": config.APPROVER_ALLOWLIST_PATH,
                "APPROVER_SOCKET_TIMEOUT": str(config.approver_socket_timeout())},
    }}}
    Path(config.APPROVER_MCP_CONFIG_PATH).write_text(json.dumps(cfg))
    try:
        os.unlink(config.APPROVER_SOCKET_PATH)
    except FileNotFoundError:
        pass
    await asyncio.start_unix_server(_handle_approval_request, path=config.APPROVER_SOCKET_PATH)
    log.info("approver tier ON: approval socket=%s mcp-config=%s",
             config.APPROVER_SOCKET_PATH, config.APPROVER_MCP_CONFIG_PATH)
