---
name: portainer
description: Control Docker containers and stacks via Portainer API. List containers, start/stop/restart, view logs, and redeploy stacks from git.
version: 0.1.1
metadata:
  openclaw:
    emoji: "🐳"
    requires:
      bins: ["python3"]
      env: []
    primaryCredential: "portainer"
  autobot:
    credential: "portainer"
    base_url_source: "user configuration or PORTAINER_URL"
    preferred_tools:
      - "portainer-containers-token"
      - "portainer-container-restart-token"
---

# 🐳 Portainer Skill

Control Docker containers and stacks through the Portainer REST API.

## Version

Current version: **0.1.1**

### Changelog

#### 0.1.1

- Validated Portainer availability from Autobot using the stored `portainer` credential.
- Confirmed endpoint discovery and running-container listing using the tokenized flow.
- Documented the preferred Autobot-safe flow:
  - retrieve the credential with `get_credential("portainer")`
  - pass the token in memory to `portainer-containers-token` or `portainer-container-restart-token`
  - never expose the token in shell commands, logs, or files
- Documented that wrappers using implicit credential access may return false missing-credential errors in some runtimes.
- Removed deployment-specific URLs, endpoint names and container inventories from the reusable template.

#### 0.1.0

- Initial Portainer skill for listing endpoints, containers and stacks, reading logs, and performing guarded operational actions.

## 🎯 Purpose

Use this skill when the user wants to inspect or operate Docker infrastructure exposed in Portainer:
- list environments/endpoints
- list running containers across all endpoints
- list containers on a chosen endpoint
- list stacks
- inspect stack details
- read container logs
- start/stop/restart containers
- redeploy git-backed stacks

## 🧠 Operating Rules

- Prefer **read-only commands first** when identifying the target.
- For “qué docker están corriendo” / “list running containers”, use `running` with no endpoint unless the user specifies one.
- For actions on containers or stacks, use the exact name or ID returned by Portainer.
- **Start / stop / restart / redeploy are operational changes**. In chat, require explicit user intent before executing them.
- Do **not** assume a single endpoint for mutations; pass the endpoint explicitly unless the user clearly established the target environment first.
- If Portainer returns an API error, relay the message exactly.

## 🔐 Authentication

This skill uses the stored credential `portainer`. The credential is a Portainer API key sent as `X-API-Key`. Do not print, persist, or pass the token through shell command arguments.

### Autobot preferred credential flow

1. Retrieve the credential in memory with `get_credential("portainer")`.
2. Pass the returned token directly to one of the token-aware tools.

```text
portainer-containers-token(
  token=<in-memory token>,
  base_url=<configured-portainer-url>,
  endpoint_id="",
  only_running=true
)
```

For container restarts:

```text
portainer-container-restart-token(
  token=<in-memory token>,
  base_url=<configured-portainer-url>,
  endpoint_id="<endpoint-id>",
  container_id="<container-id-or-name>",
  timeout=10
)
```

### Known wrapper limitation

If a wrapper using implicit credential access returns `missing_credential_portainer` while `get_credential("portainer")` succeeds, use the tokenized flow above.

## 🌐 Portainer URL

Configure the Portainer base URL through user input, workspace configuration, or `PORTAINER_URL`. Do not hard-code private deployment URLs in reusable templates.

## ⚙️ Legacy script commands

Run from the workspace after exporting `PORTAINER_API_KEY` safely in the process environment:

```bash
python3 skills/portainer/skill.py status
python3 skills/portainer/skill.py endpoints
python3 skills/portainer/skill.py running [endpoint-id]
python3 skills/portainer/skill.py containers [endpoint-id]
python3 skills/portainer/skill.py stacks
python3 skills/portainer/skill.py stack-info <stack-id>
python3 skills/portainer/skill.py redeploy <stack-id>
python3 skills/portainer/skill.py start <container-name> <endpoint-id>
python3 skills/portainer/skill.py stop <container-name> <endpoint-id>
python3 skills/portainer/skill.py restart <container-name> <endpoint-id>
python3 skills/portainer/skill.py logs <container-name> <endpoint-id> [tail-lines]
```

Use `--json` for structured output.

## 🔧 Notes

- The Python runner is self-contained and does not depend on `curl` or `jq`.
- `skill.sh` is kept for manual shell use, but token-aware tools are preferred inside Autobot.
- Endpoint IDs and `tail-lines` should be numeric.
- `redeploy` reads stack metadata first to preserve env vars and git credential linkage when available.
- If multiple endpoints contain similarly named containers, disambiguate by endpoint before acting.

## 🧱 Response Guidance

- For read operations: summarize the relevant rows cleanly.
- For running container lists: group by endpoint and include name + status.
- For action operations: report the exact target and whether Portainer confirmed success.
- For failures: include the exact API error text.

## 🔗 Reference

- [Portainer API docs](https://documentation.portainer.io/api/docs/)
