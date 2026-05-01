#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${CLOUDFLARE_API_TOKEN:-}" ]]; then
  echo "CLOUDFLARE_API_TOKEN missing in environment" >&2
  exit 1
fi

python3 - <<'PY'
import json, os, urllib.request
headers={'Authorization': 'Bearer ' + os.environ['CLOUDFLARE_API_TOKEN']}
def get(url):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())
acc = get('https://api.cloudflare.com/client/v4/accounts')
zon = get('https://api.cloudflare.com/client/v4/zones')
out = {
  'accounts': [{'id': x.get('id'), 'name': x.get('name')} for x in acc.get('result', [])],
  'zones': [
    {
      'id': x.get('id'), 'name': x.get('name'), 'status': x.get('status'), 'type': x.get('type'),
      'account_id': (x.get('account') or {}).get('id'),
      'account_name': (x.get('account') or {}).get('name')
    }
    for x in zon.get('result', [])
  ]
}
print(json.dumps(out, ensure_ascii=False, indent=2))
PY
