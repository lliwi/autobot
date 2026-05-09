#!/usr/bin/env bash
set -euo pipefail

JACKETT_URL="${JACKETT_URL:-}"
JACKETT_API_KEY="${JACKETT_API_KEY:-}"
TRANSMISSION_URL="${TRANSMISSION_URL:-}"

if [[ -z "$JACKETT_URL" || -z "$JACKETT_API_KEY" || -z "$TRANSMISSION_URL" ]]; then
  echo "Error: JACKETT_URL, JACKETT_API_KEY, and TRANSMISSION_URL must be set" >&2
  exit 1
fi

need_bin() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Error: required binary '$1' not found" >&2
    exit 1
  }
}

need_bin curl
need_bin jq

# Torznab category map
torznab_category() {
  case "${1:-}" in
    movie)    echo "2000" ;;
    tv)       echo "5000" ;;
    book)     echo "7000" ;;
    music)    echo "3000" ;;
    software) echo "4000" ;;
    *)        echo "" ;;
  esac
}

# Jackett search — returns TSV: title, seeders, magnet/link
jackett_search() {
  local query="$1"
  local category="${2:-}"
  local cat_param=""
  local cat_id
  cat_id=$(torznab_category "$category")
  [[ -n "$cat_id" ]] && cat_param="&Category[]=${cat_id}"

  local encoded_query
  encoded_query=$(jq -rn --arg q "$query" '$q | @uri')

  curl -fsS \
    "${JACKETT_URL%/}/api/v2.0/indexers/all/results?apikey=${JACKETT_API_KEY}&Query=${encoded_query}${cat_param}" \
    | jq -r '.Results // [] | sort_by(-.Seeders) | .[] | [.Title, (.Seeders | tostring), (.MagnetUri // .Link // "")] | @tsv'
}

# Transmission RPC — fetches a fresh session ID per call (stateless, safe)
transmission_rpc() {
  local payload="$1"
  local url="${TRANSMISSION_URL%/}/transmission/rpc"

  local session_id
  session_id=$(
    curl -sS -o /dev/null -D - "$url" 2>/dev/null \
      | grep -i "^X-Transmission-Session-Id:" \
      | head -1 \
      | tr -d '\r\n' \
      | sed 's/[^:]*: //'
  )

  curl -fsS -X POST \
    -H "Content-Type: application/json" \
    -H "X-Transmission-Session-Id: ${session_id}" \
    -d "$payload" \
    "$url"
}

# Add a magnet or URL to Transmission
transmission_add() {
  local target="$1"
  [[ "$target" =~ ^(magnet:|https?://) ]] || {
    echo "Error: target must be a magnet link or http/https URL" >&2
    exit 1
  }
  local payload
  payload=$(jq -n --arg url "$target" '{"method":"torrent-add","arguments":{"filename":$url,"paused":false}}')
  transmission_rpc "$payload" | jq -r '
    if .result == "success" then
      if .arguments["torrent-added"] then "✓ Added: \(.arguments["torrent-added"].name)"
      elif .arguments["torrent-duplicate"] then "Already exists: \(.arguments["torrent-duplicate"].name)"
      else "✓ Done"
      end
    else "✗ \(.result)"
    end'
}

cmd="${1:-}"
case "$cmd" in
  ping)
    printf "Jackett: "
    curl -fsS "${JACKETT_URL%/}/api/v2.0/server/config?apikey=${JACKETT_API_KEY}" \
      | jq -r '"ok (v\(.app_version))"' \
      || echo "unreachable"

    printf "Transmission: "
    transmission_rpc '{"method":"session-stats"}' \
      | jq -r '"ok (active: \(.arguments.activeTorrentCount // 0))"' \
      || echo "unreachable"
    ;;

  search)
    query="${2:-}"
    [[ -n "$query" ]] || { echo "Usage: skill.sh search <query> [category]" >&2; exit 1; }
    category="${3:-}"
    jackett_search "$query" "$category"
    ;;

  add)
    target="${2:-}"
    [[ -n "$target" ]] || { echo "Usage: skill.sh add <magnet-or-url>" >&2; exit 1; }
    transmission_add "$target"
    ;;

  add-first)
    query="${2:-}"
    [[ -n "$query" ]] || { echo "Usage: skill.sh add-first <query> [category]" >&2; exit 1; }
    category="${3:-}"
    first_line=$(jackett_search "$query" "$category" | head -n1)
    [[ -n "$first_line" ]] || { echo "No results found for: $query" >&2; exit 1; }
    first_title=$(printf '%s' "$first_line" | cut -f1)
    first_link=$(printf '%s' "$first_line" | cut -f3)
    [[ -n "$first_link" ]] || { echo "Top result has no magnet or link: $first_title" >&2; exit 1; }
    echo "Adding: $first_title"
    transmission_add "$first_link"
    ;;

  list)
    transmission_rpc \
      '{"method":"torrent-get","arguments":{"fields":["id","name","status","percentDone","rateDownload","eta"]}}' \
      | jq -r '.arguments.torrents[] | [
          .name,
          ([100 * .percentDone | floor | tostring] | .[0] + "%"),
          (if .rateDownload > 0 then (.rateDownload / 1024 | floor | tostring) + " KB/s" else "-" end),
          (if .eta > 0 then (.eta | tostring) + "s" else "-" end)
        ] | @tsv'
    ;;

  *)
    cat <<'EOF'
Jackett + Transmission CLI

Usage: bash skills/jackett-transmission/skill.sh <command> [args]

Commands:
  ping                          Check connectivity with Jackett and Transmission
  search <query> [category]     Search torrents via Jackett (sorted by seeders)
  add <magnet-or-url>           Add a torrent to Transmission
  add-first <query> [category]  Search and add the top result automatically
  list                          List active torrents in Transmission

Categories: movie, tv, book, music, software

Environment:
  JACKETT_URL        Base URL of your Jackett instance
  JACKETT_API_KEY    Jackett API key (keep in memory, never log)
  TRANSMISSION_URL   Base URL of your Transmission instance
EOF
    ;;
esac
