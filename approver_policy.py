"""Pure approver policy — shared by bot.py (the bridge) and mcp_approver.py (the MCP
server spawned per call by `claude -p`). NO third-party deps and NO discord import, so
the lightweight approver subprocess can import it without pulling in the bridge runtime.

The policy decides, for a tool call claude wants to make, whether to auto-`allow` it or
`escalate` it to a human on Discord. Denial is never produced here — it comes only from a
human ❌ or a fail-closed timeout (see mcp_approver.py), so the allow-list can never be the
thing that *grants* a dangerous command; it only removes the approval friction for known-safe ones.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# Tools that never need per-command approval. Read/edit/search within the mounted project
# dirs is the whole point of the execution tier; the mount boundary + permissions.deny
# already bound what they can touch. Bash is deliberately absent — it is policy-checked by command.
DEFAULT_AUTO_ALLOW_TOOLS = {
    "Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Glob", "Grep", "LS", "TodoWrite",
}

# A compound command must have EVERY segment allow-listed, else the whole thing escalates —
# so an allow-listed segment can never green-light a piggy-backed non-listed one
# (`pytest && curl evil` escalates). Split on the shell operators that chain commands.
_SEGMENT_SPLIT = re.compile(r"&&|\|\||\||;|\n")

# Constructs that smuggle a second command past the segment splitter (command/process
# substitution, var expansion, redirects). The splitter is a plain regex and cannot see
# inside these, so any segment containing one is never auto-allowed — it escalates to a
# human, who sees the literal command. (`pytest "$(curl evil)"` → escalate, not allow.)
_SUBSTITUTION_METACHARS = ("$(", "`", "${", ">", "<")


def load_allowlist(path: str | Path) -> dict:
    """Load the allow-list JSON. Returns {auto_allow_tools:set, bash_prefixes:list}."""
    data = json.loads(Path(path).read_text())
    return {
        "auto_allow_tools": set(data.get("auto_allow_tools") or DEFAULT_AUTO_ALLOW_TOOLS),
        "bash_prefixes": list(data.get("bash_prefixes") or []),
    }


def _segment_allowlisted(segment: str, prefixes: list[str]) -> bool:
    seg = segment.strip()
    if not seg:
        return False
    # a segment hiding a substitution/redirect is never auto-allowed (the splitter can't
    # see the smuggled command) — fall through to human approval instead
    if any(m in seg for m in _SUBSTITUTION_METACHARS):
        return False
    # match a prefix exactly, or as a whole leading token-run followed by a space/args
    return any(seg == p or seg.startswith(p + " ") for p in prefixes)


def command_allowlisted(command: str, prefixes: list[str]) -> bool:
    """True iff EVERY segment of a (possibly compound) command matches an allow-list prefix."""
    segments = [s for s in _SEGMENT_SPLIT.split(command or "") if s.strip()]
    if not segments:
        return False
    return all(_segment_allowlisted(s, prefixes) for s in segments)


def decide(tool_name: str, tool_input: dict | None, allowlist: dict) -> str:
    """Return 'allow' (auto-approve) or 'escalate' (ask a human). Never 'deny' — that is a
    human/timeout outcome, kept out of the static policy so the allow-list cannot grant."""
    if tool_name in allowlist["auto_allow_tools"]:
        return "allow"
    if tool_name == "Bash":
        command = (tool_input or {}).get("command", "")
        if command_allowlisted(command, allowlist["bash_prefixes"]):
            return "allow"
    return "escalate"
