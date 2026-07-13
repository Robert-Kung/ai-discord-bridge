#!/bin/sh
# Sync each account's live OAuth credential into the staging dir the executor
# bind-mounts (host cron, every minute — see SECURITY.md §9).
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
    dst="$dst_dir/.credentials.json"
    if [ ! -f "$dst" ] || [ "$src" -nt "$dst" ]; then
        mkdir -p "$dst_dir"
        tmp="$dst_dir/.credentials.json.tmp.$$"
        cp "$src" "$tmp" && chmod 600 "$tmp" && mv "$tmp" "$dst"
    fi
}

sync_one "$HOME/.claude/.credentials.json"   "$HOME/.claude-bot-creds/a"
sync_one "$HOME/.claude-b/.credentials.json" "$HOME/.claude-bot-creds/b"
