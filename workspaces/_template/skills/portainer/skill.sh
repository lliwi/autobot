#!/usr/bin/env bash
set -euo pipefail

PORTAINER_URL="${PORTAINER_URL:-}"
PORTAINER_API_KEY="${PORTAINER_API_KEY:-}"

if [[ -z "$PORTAINER_URL" || -z "$PORTAINER_API_KEY" ]]; then
  echo "Error: PORTAINER_URL and PORTAINER_API_KEY must be set" >&2
  exit 1
fi

API="${PORTAINER_URL%/}/api"
AUTH_HEADER="X-API-Key: $PORTAINER_API_KEY"

need_bin() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Error: required binary '$1' not found" >&2
    exit 1
  }
}

need_bin curl
need_bin jq

api_get() {
  curl -fsS -H "$AUTH_HEADER" "$API$1"
}

api_post() {
  curl -fsS -X POST -H "$AUTH_HEADER" -H "Content-Type: application/json" "$API$1" -d "$2"
}

api_put() {
  curl -fsS -X PUT -H "$AUTH_HEADER" -H "Content-Type: application/json" "$API$1" -d "$2"
}

require_numeric() {
  local value="$1"
  local label="$2"
  [[ "$value" =~ ^[0-9]+$ ]] || {
    echo "Error: $label must be numeric" >&2
    exit 1
  }
}

find_container_id() {
  local endpoint="$1"
  local container="$2"
  api_get "/endpoints/$endpoint/docker/containers/json?all=true" | jq -r --arg name "/$container" '.[] | select(.Names[0] == $name) | .Id' | head -n1
}

cmd="${1:-}"
case "$cmd" in
  status)
    api_get "/status" | jq -r '"Portainer v\(.Version)"'
    ;;

  endpoints|envs)
    api_get "/endpoints" | jq -r '.[] | "\(.Id): \(.Name) (\(if .Type == 1 then "local" else "remote" end)) - \(if .Status == 1 then "✓ online" else "✗ offline" end)"'
    ;;

  running|ps)
    endpoint="${2:-}"
    filter="?status=running"
    if [[ -n "$endpoint" ]]; then
      api_get "/endpoints/$endpoint/docker/containers/json$filter" | jq -r '.[] | [(.Names[0] | ltrimstr("/")), .State, .Status] | @tsv'
    else
      for ep_id in $(api_get "/endpoints" | jq -r '.[].Id'); do
        api_get "/endpoints/$ep_id/docker/containers/json$filter" | jq -r --arg ep "$ep_id" '.[] | [$ep, (.Names[0] | ltrimstr("/")), .State, .Status] | @tsv'
      done
    fi
    ;;

  containers)
    endpoint="${2:-}"
    [[ -n "$endpoint" ]] || { echo "Usage: skill.sh containers <endpoint-id>" >&2; exit 1; }
    require_numeric "$endpoint" "endpoint-id"
    api_get "/endpoints/$endpoint/docker/containers/json?all=true" | jq -r '.[] | [(.Names[0] | ltrimstr("/")), .State, .Status] | @tsv'
    ;;

  stacks)
    api_get "/stacks" | jq -r '.[] | "\(.Id): \(.Name) - \(if .Status == 1 then "✓ active" else "✗ inactive" end)"'
    ;;

  stack-info)
    stack_id="${2:-}"
    [[ -n "$stack_id" ]] || { echo "Usage: skill.sh stack-info <stack-id>" >&2; exit 1; }
    require_numeric "$stack_id" "stack-id"
    api_get "/stacks/$stack_id" | jq '{Id, Name, Status, EndpointId, GitConfig: (.GitConfig.URL // null), UpdateDate}'
    ;;

  redeploy)
    stack_id="${2:-}"
    [[ -n "$stack_id" ]] || { echo "Usage: skill.sh redeploy <stack-id>" >&2; exit 1; }
    require_numeric "$stack_id" "stack-id"
    stack_info="$(api_get "/stacks/$stack_id")"
    endpoint_id="$(printf '%s' "$stack_info" | jq -r '.EndpointId')"
    env_vars="$(printf '%s' "$stack_info" | jq -c '.Env // []')"
    git_cred_id="$(printf '%s' "$stack_info" | jq -r '.GitConfig.Authentication.GitCredentialID // 0')"
    payload="$(jq -n --argjson env "$env_vars" --argjson gitCredId "$git_cred_id" '{env: $env, prune: false, pullImage: true, repositoryAuthentication: true, repositoryGitCredentialID: $gitCredId}')"
    result="$(api_put "/stacks/$stack_id/git/redeploy?endpointId=$endpoint_id" "$payload")"
    printf '%s' "$result" | jq -e '.Id' >/dev/null 2>&1 || {
      echo "✗ Redeploy failed" >&2
      printf '%s\n' "$result"
      exit 1
    }
    stack_name="$(printf '%s' "$result" | jq -r '.Name')"
    echo "✓ Stack '$stack_name' redeployed successfully"
    ;;

  start|stop|restart)
    action="$cmd"
    container="${2:-}"
    endpoint="${3:-}"
    [[ -n "$container" && -n "$endpoint" ]] || { echo "Usage: skill.sh $action <container-name> <endpoint-id>" >&2; exit 1; }
    require_numeric "$endpoint" "endpoint-id"
    container_id="$(find_container_id "$endpoint" "$container")"
    [[ -n "$container_id" ]] || { echo "✗ Container '$container' not found on endpoint $endpoint" >&2; exit 1; }
    api_post "/endpoints/$endpoint/docker/containers/$container_id/$action" '{}' >/dev/null
    echo "✓ Container '$container' ${action}ed"
    ;;

  logs)
    container="${2:-}"
    endpoint="${3:-}"
    tail_lines="${4:-100}"
    [[ -n "$container" && -n "$endpoint" ]] || { echo "Usage: skill.sh logs <container-name> <endpoint-id> [tail-lines]" >&2; exit 1; }
    require_numeric "$endpoint" "endpoint-id"
    require_numeric "$tail_lines" "tail-lines"
    container_id="$(find_container_id "$endpoint" "$container")"
    [[ -n "$container_id" ]] || { echo "✗ Container '$container' not found on endpoint $endpoint" >&2; exit 1; }
    curl -fsS -H "$AUTH_HEADER" "$API/endpoints/$endpoint/docker/containers/$container_id/logs?stdout=true&stderr=true&tail=$tail_lines"
    ;;

  *)
    cat <<'EOF'
Portainer CLI - Control Docker via Portainer API

Usage: bash skills/portainer/skill.sh <command> [args]

Commands:
  status
  endpoints
  running|ps [endpoint-id]   List running containers (all endpoints if omitted)
  containers <endpoint-id>
  stacks
  stack-info <id>
  redeploy <stack-id>
  start <container> <endpoint-id>
  stop <container> <endpoint-id>
  restart <container> <endpoint-id>
  logs <container> <endpoint-id> [tail-lines]
EOF
    ;;
esac
