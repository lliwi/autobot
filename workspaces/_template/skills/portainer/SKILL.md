---
name: portainer
description: Control Docker containers and stacks via Portainer API. List containers, start/stop/restart, view logs, and redeploy stacks from git.
metadata:
  openclaw:
    emoji: "🐳"
    requires:
      bins: ["python3"]
      env: []
    primaryCredential: "portainer"
---

# 🐳 Portainer Skill

Control Docker containers and stacks through the Portainer REST API.

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

This skill uses:
- stored credential `portainer`, passed as `PORTAINER_API_KEY` when invoking the script
- optional environment variable `PORTAINER_URL`

If `PORTAINER_URL` is absent, the Python runner defaults to:

```bash
http://192.168.0.20:9000
```

Expected header:

```bash
X-API-Key: <token>
```

Do not print or persist the token.

## ⚙️ Commands

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

Use `--json` for structured output:

```bash
python3 skills/portainer/skill.py --json running
```

## 📖 Examples

### Status

```bash
python3 skills/portainer/skill.py status
```

### List endpoints

```bash
python3 skills/portainer/skill.py endpoints
```

### List running containers across all endpoints

```bash
python3 skills/portainer/skill.py running
```

### List all containers on endpoint 5

```bash
python3 skills/portainer/skill.py containers 5
```

### Redeploy a stack

```bash
python3 skills/portainer/skill.py redeploy 25
```

### Restart a container

```bash
python3 skills/portainer/skill.py restart minecraft 4
```

## 🔧 Notes

- The Python runner is self-contained and does not depend on `curl` or `jq`.
- `skill.sh` is kept for manual shell use, but the Python runner is preferred inside Autobot.
- The API key is passed in the environment when invoking the script.
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
