#!/usr/bin/env python3
"""Run `cswap --list --token-status` on the HOST, parse the 5h/7d quota for
each account, and write JSON to the Discord bridge state dir (which is mounted
into the container). The bot's !state reads this file — cswap itself can't run
inside the container.

Install as a cron job, e.g. every 10 min:
  */10 * * * * /home/user/ai-discord-bridge/scripts/refresh-cswap-usage.py
"""
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

OUT = Path.home() / ".claude-shared" / "discord-state" / "cswap-usage.json"
# cron has a minimal PATH; resolve cswap explicitly (it lives in ~/.local/bin)
CSWAP = shutil.which("cswap") or str(Path.home() / ".local" / "bin" / "cswap")

# account header:    "  1: you@example.com [Org] (active)"
HEADER = re.compile(r"^\s*(\d+):\s+(\S+).*?(\(active\))?\s*$")
# quota line:        " ├ 5h:  35%   resets 20:10         in 4h 30m"
QUOTA = re.compile(r"(5h|7d):\s+(\d+)%\s+resets\s+(.+?)\s+in\s+(.+?)\s*$")


def main() -> None:
    try:
        out = subprocess.run(
            [CSWAP, "--list", "--token-status"],
            capture_output=True, text=True, timeout=60,
        ).stdout
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        OUT.write_text(json.dumps({"generated_at": int(time.time()), "error": str(e),
                                   "accounts": []}))
        return

    accounts: list[dict] = []
    cur: dict | None = None
    for line in out.splitlines():
        h = HEADER.match(line)
        if h and not QUOTA.search(line):
            cur = {"slot": int(h.group(1)), "email": h.group(2),
                   "active": bool(h.group(3))}
            accounts.append(cur)
            continue
        q = QUOTA.search(line)
        if q and cur is not None:
            win, pct, resets, in_ = q.groups()
            key = "h5" if win == "5h" else "d7"
            cur[f"{key}_pct"] = int(pct)
            cur[f"{key}_resets"] = resets.strip()
            cur[f"{key}_in"] = in_.strip()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"generated_at": int(time.time()),
                               "accounts": accounts}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
