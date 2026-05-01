#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${CLOUDFLARE_API_TOKEN:-}" ]]; then
  echo "CLOUDFLARE_API_TOKEN missing in environment" >&2
  exit 1
fi

python3 - <<'PY'
import json, os, urllib.request, urllib.error
req = urllib.request.Request(
    'https://api.cloudflare.com/client/v4/user/tokens/verify',
    headers={'Authorization': 'Bearer ' + os.environ['CLOUDFLARE_API_TOKEN']}
)
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        j = json.loads(r.read().decode())
except urllib.error.HTTPError as e:
    body = e.read().decode(errors='replace')
    print(json.dumps({'success': False, 'http_status': e.code, 'error_body': body}, ensure_ascii=False, indent=2))
    raise SystemExit(1)
out = {
    'success': j.get('success'),
    'result': j.get('result'),
    'errors': j.get('errors', []),
    'messages': j.get('messages', []),
    'note': 'Token verification confirms authentication state, not full authorization to every accounts/zones/tunnels endpoint.'
}
print(json.dumps(out, ensure_ascii=False, indent=2))
PY
