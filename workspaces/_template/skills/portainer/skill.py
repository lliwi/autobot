#!/usr/bin/env python3
"""Portainer CLI for the Autobot workspace.

Read-only commands are safe by default. Mutating commands still require the
Portainer API key via PORTAINER_API_KEY and should only be invoked after an
explicit user request.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional

DEFAULT_PORTAINER_URL = "http://192.168.0.20:9000"
ALLOWED_DEFAULT_HOSTS = {"192.168.0.20", "192.168.0.40", "localhost", "127.0.0.1"}

PORTAINER_URL = ""
PORTAINER_API_KEY = ""
API_BASE = ""
OUTPUT_JSON = False


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def usage() -> None:
    print(
        """Portainer CLI - Control Docker via Portainer API

Usage: python skills/portainer/skill.py [--json] <command> [args]

Commands:
  status
  endpoints
  containers [endpoint-id]       List containers. If endpoint-id is omitted, list all endpoints.
  running [endpoint-id]          List only running containers. If endpoint-id is omitted, list all endpoints.
  stacks
  stack-info <id>
  redeploy <stack-id>
  start <container> <endpoint-id>
  stop <container> <endpoint-id>
  restart <container> <endpoint-id>
  logs <container> <endpoint-id> [tail-lines]

Environment:
  PORTAINER_API_KEY              Required for API calls.
  PORTAINER_URL                  Optional; defaults to http://192.168.0.20:9000.
"""
    )


def init_env() -> None:
    global PORTAINER_URL, PORTAINER_API_KEY, API_BASE
    PORTAINER_URL = (os.environ.get("PORTAINER_URL") or DEFAULT_PORTAINER_URL).strip().rstrip("/")
    PORTAINER_API_KEY = os.environ.get("PORTAINER_API_KEY", "").strip()
    if not PORTAINER_API_KEY:
        eprint("Error: PORTAINER_API_KEY must be set")
        sys.exit(1)
    if not PORTAINER_URL:
        eprint("Error: PORTAINER_URL is empty")
        sys.exit(1)
    API_BASE = f"{PORTAINER_URL}/api"


def require_numeric(value: str, label: str) -> int:
    if not str(value).isdigit():
        eprint(f"Error: {label} must be numeric")
        sys.exit(1)
    return int(value)


def api_request(method: str, path: str, payload=None, expect_json: bool = True):
    url = f"{API_BASE}{path}"
    body = None
    headers = {"X-API-Key": PORTAINER_API_KEY, "Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            if not expect_json:
                return raw.decode("utf-8", errors="replace")
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        eprint(f"HTTP {exc.code}: {detail}")
        sys.exit(1)
    except urllib.error.URLError as exc:
        eprint(f"Network error: {exc.reason}")
        sys.exit(1)
    except json.JSONDecodeError as exc:
        eprint(f"Parse error: {exc}")
        sys.exit(1)


def emit(data: Any) -> None:
    if OUTPUT_JSON:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    if isinstance(data, str):
        print(data)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def endpoint_summary(ep: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": ep.get("Id"),
        "name": ep.get("Name"),
        "type": "local" if ep.get("Type") == 1 else "remote",
        "status": "online" if ep.get("Status") == 1 else "offline",
        "url": ep.get("URL"),
    }


def list_endpoints() -> List[Dict[str, Any]]:
    endpoints = api_request("GET", "/endpoints") or []
    return [endpoint_summary(ep) for ep in endpoints]


def safe_container(item: Dict[str, Any]) -> Dict[str, Any]:
    names = item.get("Names") or []
    labels = item.get("Labels") or {}
    return {
        "id": (item.get("Id") or "")[:12],
        "name": (names[0].lstrip("/") if names else (item.get("Name") or "")),
        "image": item.get("Image") or "",
        "state": item.get("State") or "unknown",
        "status": item.get("Status") or "",
        "stack": labels.get("com.docker.compose.project") or labels.get("io.portainer.stack.name") or "",
    }


def containers_for_endpoint(endpoint_id: int, only_running: bool = False) -> List[Dict[str, Any]]:
    qs = urllib.parse.urlencode({"all": "true"})
    items = api_request("GET", f"/endpoints/{endpoint_id}/docker/containers/json?{qs}") or []
    rows = [safe_container(item) for item in items]
    if only_running:
        rows = [row for row in rows if str(row.get("state", "")).lower() == "running"]
    return sorted(rows, key=lambda row: row.get("name") or "")


def find_container(endpoint_id: int, container_name: str):
    containers = api_request("GET", f"/endpoints/{endpoint_id}/docker/containers/json?all=true")
    target = f"/{container_name}"
    for item in containers:
        names = item.get("Names") or []
        if target in names:
            return item
    return None


def cmd_status():
    status = api_request("GET", "/status")
    data = {"version": status.get("Version", "unknown"), "base_url": PORTAINER_URL}
    if OUTPUT_JSON:
        emit(data)
    else:
        print(f"Portainer v{data['version']} ({PORTAINER_URL})")


def cmd_endpoints():
    endpoints = list_endpoints()
    if OUTPUT_JSON:
        emit({"base_url": PORTAINER_URL, "endpoints": endpoints})
        return
    for ep in endpoints:
        state = "✓ online" if ep.get("status") == "online" else "✗ offline"
        print(f"{ep.get('id')}: {ep.get('name')} ({ep.get('type')}) - {state}")


def cmd_containers(endpoint_id: Optional[int], only_running: bool = False):
    endpoints = list_endpoints()
    if endpoint_id is not None:
        endpoints = [ep for ep in endpoints if str(ep.get("id")) == str(endpoint_id)]
        if not endpoints:
            eprint(f"Error: endpoint {endpoint_id} not found")
            sys.exit(1)

    result = []
    for ep in endpoints:
        eid = int(ep.get("id"))
        rows = containers_for_endpoint(eid, only_running=only_running)
        result.append({"endpoint": ep, "count": len(rows), "containers": rows})

    if OUTPUT_JSON:
        emit({"base_url": PORTAINER_URL, "only_running": only_running, "results": result})
        return

    for group in result:
        ep = group["endpoint"]
        print(f"# {ep.get('name')} (endpoint {ep.get('id')}) - {group['count']} containers")
        for item in group["containers"]:
            print(f"{item['name']}\t{item['state']}\t{item['status']}\t{item['image']}")


def cmd_stacks():
    stacks = api_request("GET", "/stacks") or []
    if OUTPUT_JSON:
        emit({"stacks": stacks})
        return
    for stack in stacks:
        state = "✓ active" if stack.get("Status") == 1 else "✗ inactive"
        print(f"{stack.get('Id')}: {stack.get('Name')} - {state}")


def cmd_stack_info(stack_id: int):
    stack = api_request("GET", f"/stacks/{stack_id}")
    out = {
        "Id": stack.get("Id"),
        "Name": stack.get("Name"),
        "Status": stack.get("Status"),
        "EndpointId": stack.get("EndpointId"),
        "GitConfig": ((stack.get("GitConfig") or {}).get("URL")),
        "UpdateDate": stack.get("UpdateDate"),
    }
    emit(out)


def cmd_redeploy(stack_id: int):
    stack = api_request("GET", f"/stacks/{stack_id}")
    endpoint_id = stack.get("EndpointId")
    git_auth = (((stack.get("GitConfig") or {}).get("Authentication") or {}).get("GitCredentialID")) or 0
    payload = {
        "env": stack.get("Env") or [],
        "prune": False,
        "pullImage": True,
        "repositoryAuthentication": True,
        "repositoryGitCredentialID": git_auth,
    }
    result = api_request("PUT", f"/stacks/{stack_id}/git/redeploy?endpointId={endpoint_id}", payload)
    if result.get("Id"):
        print(f"✓ Stack '{result.get('Name')}' redeployed successfully")
        return
    eprint("✗ Redeploy failed")
    eprint(json.dumps(result, ensure_ascii=False))
    sys.exit(1)


def cmd_container_action(action: str, container_name: str, endpoint_id: int):
    container = find_container(endpoint_id, container_name)
    if not container:
        eprint(f"✗ Container '{container_name}' not found on endpoint {endpoint_id}")
        sys.exit(1)
    container_id = container.get("Id")
    api_request("POST", f"/endpoints/{endpoint_id}/docker/containers/{container_id}/{action}", payload={})
    verb = {"start": "started", "stop": "stopped", "restart": "restarted"}[action]
    print(f"✓ Container '{container_name}' {verb}")


def cmd_logs(container_name: str, endpoint_id: int, tail_lines: int):
    container = find_container(endpoint_id, container_name)
    if not container:
        eprint(f"✗ Container '{container_name}' not found on endpoint {endpoint_id}")
        sys.exit(1)
    container_id = container.get("Id")
    query = urllib.parse.urlencode({"stdout": "true", "stderr": "true", "tail": str(tail_lines)})
    data = api_request("GET", f"/endpoints/{endpoint_id}/docker/containers/{container_id}/logs?{query}", expect_json=False)
    print(data, end="" if data.endswith("\n") else "\n")


def parse_args(argv: List[str]) -> List[str]:
    global OUTPUT_JSON
    args = list(argv)
    if "--json" in args:
        OUTPUT_JSON = True
        args = [arg for arg in args if arg != "--json"]
    return args


if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    if not args:
        usage()
        sys.exit(0)

    if args[0] in {"-h", "--help"}:
        usage()
        sys.exit(0)

    init_env()

    cmd = args[0]
    if cmd == "status":
        cmd_status()
    elif cmd in {"endpoints", "envs"}:
        cmd_endpoints()
    elif cmd == "containers":
        endpoint = require_numeric(args[1], "endpoint-id") if len(args) > 1 else None
        cmd_containers(endpoint, only_running=False)
    elif cmd in {"running", "ps"}:
        endpoint = require_numeric(args[1], "endpoint-id") if len(args) > 1 else None
        cmd_containers(endpoint, only_running=True)
    elif cmd == "stacks":
        cmd_stacks()
    elif cmd == "stack-info":
        if len(args) < 2:
            eprint("Usage: skill.py stack-info <stack-id>")
            sys.exit(1)
        cmd_stack_info(require_numeric(args[1], "stack-id"))
    elif cmd == "redeploy":
        if len(args) < 2:
            eprint("Usage: skill.py redeploy <stack-id>")
            sys.exit(1)
        cmd_redeploy(require_numeric(args[1], "stack-id"))
    elif cmd in {"start", "stop", "restart"}:
        if len(args) < 3:
            eprint(f"Usage: skill.py {cmd} <container-name> <endpoint-id>")
            sys.exit(1)
        cmd_container_action(cmd, args[1], require_numeric(args[2], "endpoint-id"))
    elif cmd == "logs":
        if len(args) < 3:
            eprint("Usage: skill.py logs <container-name> <endpoint-id> [tail-lines]")
            sys.exit(1)
        tail = require_numeric(args[3], "tail-lines") if len(args) > 3 else 100
        cmd_logs(args[1], require_numeric(args[2], "endpoint-id"), tail)
    else:
        usage()
        sys.exit(1)
