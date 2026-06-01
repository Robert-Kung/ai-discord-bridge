#!/bin/bash
# archive-old-jsonl.sh — 把 >30 天的 claude session jsonl 移到 archive
# 給 cron 用：每週跑一次
#
# 安裝：
#   crontab -e
#   0 3 * * 0 /home/user/ai-discord-bridge/scripts/archive-old-jsonl.sh >> /home/user/.claude-archive/archive.log 2>&1

set -e
ARCHIVE=$HOME/.claude-archive
mkdir -p "$ARCHIVE"

count=0
for dir in $HOME/.claude/projects $HOME/.claude-b/projects; do
    [ -d "$dir" ] || continue
    while IFS= read -r f; do
        rel=${f#$HOME/}
        target=$ARCHIVE/${rel}
        mkdir -p "$(dirname "$target")"
        mv "$f" "$target"
        count=$((count + 1))
    done < <(find "$dir" -name '*.jsonl' -type f -mtime +30)
done

echo "[$(date -Iseconds)] archived $count jsonl files to $ARCHIVE"
