#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <account_id>" >&2
  exit 2
fi
ACCOUNT_ID="$1"

if [[ ! "$ACCOUNT_ID" =~ ^[A-Za-z0-9_-]+$ ]]; then
  echo "Invalid account_id format" >&2
  exit 2
fi

if [[ -z "${CLOUDFLARE_API_TOKEN:-}" ]]; then
  echo "CLOUDFLARE_API_TOKEN missing in environment" >&2
  exit 1
fi

ACCOUNT_ID="$ACCOUNT_ID" python3 - <<'PY'
import json, os, urllib.request, urllib.parse
account_id = os.environ['ACCOUNT_ID']
req = urllib.request.Request(
    f'https://api.cloudflare.com/client/v4/accounts/{urllib.parse.quote(account_id)}/cfd_tunnel',
    headers={'Authorization': 'Bearer ' + os.environ['CLOUDFLARE_API_TOKEN']}
)
with urllib.request.urlopen(req, timeout=30) as r:
    j = json.loads(r.read().decode())
out = {
  'success': j.get('success'),
  'result': [
    {'id': x.get('id'), 'name': x.get('name'), 'status': x.get('status'), 'created_at': x.get('created_at')}
    for x in j.get('result', [])
  ],
  'errors': j.get('errors', [])
}
print(json.dumps(out, ensure_ascii=False, indent=2))
PY
