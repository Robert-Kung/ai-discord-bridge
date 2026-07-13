#!/bin/sh
# Sync the bots' read-only staged mounts (host cron, every minute — SECURITY.md §9):
#   • each account's live OAuth credential  → ~/.claude-bot-creds/{a,b}/
#   • the thin project-plan index           → ~/.claude-bot-plan/ (presented in-container
#     at ~/.claude-shared/memory/project_plan.md via the staging-dir mount)
#
# WHY A STAGING DIR: the claude CLI refreshes credentials by write-tmp+rename,
# which mints a NEW inode. A single-file bind mount pins the OLD inode, so the
# container's view goes permanently stale after the first host-side refresh and
# every call 401s (found live 2026-07-13). A DIRECTORY mount resolves by name
# on every open, so the atomic rename below is always visible in-container.
set -eu

sync_one() {
    src="$1"; dst_dir="$2"
    [ -f "$src" ] || return 0
    dst="$dst_dir/$(basename "$src")"
    if [ ! -f "$dst" ] || [ "$src" -nt "$dst" ]; then
        mkdir -p "$dst_dir"
        tmp="$dst.tmp.$$"
        cp "$src" "$tmp" && chmod 600 "$tmp" && mv "$tmp" "$dst"
    fi
}

sync_one "$HOME/.claude/.credentials.json"   "$HOME/.claude-bot-creds/a"
sync_one "$HOME/.claude-b/.credentials.json" "$HOME/.claude-bot-creds/b"

sync_one "$HOME/.claude-shared/memory/project_plan.md" "$HOME/.claude-bot-plan"
