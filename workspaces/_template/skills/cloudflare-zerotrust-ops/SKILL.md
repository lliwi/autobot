---
name: cloudflare-zerotrust-ops
description: Read-first Cloudflare Zero Trust operations from an API token credential. Use when validating token access, listing accounts/zones, and inspecting tunnels safely.
---

# Cloudflare Zero Trust Ops

Use this skill for controlled Cloudflare operations with minimum privilege.

## Preconditions

- Store the API token as an agent credential. In this agent the current credential name is `cloudflare`; `cloudflare_api_token` is also acceptable if you later rename it.
- Never print or expose token values.
- Prefer read/check first, then propose write changes.
- This workspace runtime may not have `curl`, so the bundled scripts use `python3` standard library HTTP calls.
- Before sending Cloudflare operational data to external systems such as Notion, get explicit user confirmation.

## Supported actions

Current bundled scripts are read-oriented:
- verify token validity
- list accessible accounts and zones
- list tunnels for a known account

Write actions for DNS/WAF/Zero Trust are intentionally out of scope until explicitly implemented and approved.

## Workflow

1. Fetch the stored credential from the agent. Current default: `cloudflare`.

2. Verify token:
```bash
CLOUDFLARE_API_TOKEN=... bash skills/cloudflare-zerotrust-ops/scripts/verify_token.sh
```
This confirms token authentication state. It does not guarantee authorization to every Cloudflare endpoint.

3. Discover accounts/zones:
```bash
CLOUDFLARE_API_TOKEN=... bash skills/cloudflare-zerotrust-ops/scripts/list_accounts_zones.sh
```

4. Inspect tunnels (if account_id known):
```bash
CLOUDFLARE_API_TOKEN=... bash skills/cloudflare-zerotrust-ops/scripts/list_tunnels.sh <account_id>
```

5. For future write actions, require explicit confirmation with impact + rollback.

## Safety rules

- Do not perform destructive changes without explicit user approval.
- Log only IDs, names and statuses; never secrets.
- Do not send Cloudflare operational data to third-party services unless the user explicitly asks for it.
- Do not export the token globally in a long-lived shell session.
- Do not use shell tracing such as `set -x` while passing secrets.

## Safe execution note

The shell scripts expect `CLOUDFLARE_API_TOKEN` in the process environment. On this platform, stored credentials are visible to the root agent but not always injected into shell/tool runtimes automatically. Safe pattern:

- fetch the stored credential in the root agent
- if using this agent as configured today, read credential `cloudflare`
- pass it to the script only for that single process invocation
- never write it to workspace files
- never echo the token value in output
