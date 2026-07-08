#!/usr/bin/env bash
# Force the NEXT `claude` call to refresh its OAuth token — for the egress phase-1
# cutover experiment (SECURITY.md §6 "Operator cutover"): the goal is to observe,
# in the egress proxy's log, which host the refresh actually contacts.
#
# What it does: backs up <config-dir>/.credentials.json, then rewrites ONLY the
# claudeAiOauth.expiresAt timestamp into the past. The tokens themselves are not
# touched; the CLI sees "expired", performs a refresh, and writes fresh
# credentials back over the file (run this against the HOST file, which is
# writable — inside the container the credential is a :ro single-file mount).
#
#   usage: scripts/expire-oauth-token.sh ~/.claude-b
set -euo pipefail
dir="${1:?usage: expire-oauth-token.sh <claude-config-dir>}"
f="$dir/.credentials.json"
[ -f "$f" ] || { echo "no credentials file at $f" >&2; exit 1; }
bak="$f.bak-$(date +%Y%m%d-%H%M%S)"
cp -p "$f" "$bak"
python3 - "$f" <<'EOF'
import json, sys
path = sys.argv[1]
with open(path) as fh:
    data = json.load(fh)
oauth = data.get("claudeAiOauth")
if not isinstance(oauth, dict) or "expiresAt" not in oauth:
    sys.exit(f"unexpected credentials shape in {path}: no claudeAiOauth.expiresAt "
             "— refusing to touch it (restore from the .bak file)")
oauth["expiresAt"] = 1  # epoch ms, long past -> next call must refresh
with open(path, "w") as fh:
    json.dump(data, fh)
EOF
echo "OAuth expiry forced in $f"
echo "backup: $bak"
