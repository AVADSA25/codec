#!/usr/bin/env bash
# Rotate CODEC OAuth state: revokes ALL tokens and registered clients.
# Use when: traveling, suspected token leak, lost laptop, giving a demo.
#
# Side effects:
#   - Every connected claude.ai / Claude Desktop / Claude Code must re-authorize
#   - Old ~/.codec/oauth_state.json is backed up to oauth_state.json.revoked.<ts>
#   - codec-mcp-http is restarted so in-memory state is flushed
#
# Run:  bash scripts/rotate_secrets.sh
set -euo pipefail

STATE="${HOME}/.codec/oauth_state.json"
TS=$(date -u +%Y%m%dT%H%M%SZ)

echo "[rotate] CODEC OAuth state rotation — ${TS}"

if [ -f "$STATE" ]; then
  CLIENTS=$(python3 -c "import json; d=json.load(open('$STATE')); print(len(d.get('clients',{})))" 2>/dev/null || echo "?")
  TOKENS=$(python3 -c "import json; d=json.load(open('$STATE')); print(len(d.get('access_tokens',{})))" 2>/dev/null || echo "?")
  BACKUP="${STATE}.revoked.${TS}"
  mv "$STATE" "$BACKUP"
  chmod 600 "$BACKUP" 2>/dev/null || true
  echo "[rotate] revoked $CLIENTS client(s), $TOKENS token(s)"
  echo "[rotate] backup → $BACKUP"
else
  echo "[rotate] no existing state file — nothing to revoke"
fi

# Write empty fresh state so provider starts clean
python3 -c "
import json, os, stat
path = os.path.expanduser('~/.codec/oauth_state.json')
json.dump({'clients':{}, 'access_tokens':{}, 'refresh_tokens':{},
           'access_to_refresh':{}, 'refresh_to_access':{}}, open(path,'w'))
os.chmod(path, stat.S_IRUSR|stat.S_IWUSR)
print('[rotate] fresh empty state written')
"

if command -v pm2 >/dev/null 2>&1; then
  pm2 restart codec-mcp-http --update-env >/dev/null 2>&1 && \
    echo "[rotate] codec-mcp-http restarted"
else
  echo "[rotate] pm2 not found — restart codec-mcp-http manually"
fi

echo ""
echo "Next: reconnect each Claude client (claude.ai, Desktop, Code) —"
echo "      they will auto-register and obtain fresh tokens."
