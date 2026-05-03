"""REST client for the kali-server-mcp Flask API.

kali-server-mcp is a Flask app that exposes Kali Linux tools over HTTP:
  GET  /health               — health check + tool availability
  POST /api/command          — run any shell command
  POST /api/tools/nmap       — nmap scan
  POST /api/tools/gobuster   — gobuster directory/DNS brute-force
  POST /api/tools/dirb       — dirb directory brute-force
  POST /api/tools/nikto      — nikto web scanner
  POST /api/tools/sqlmap     — sqlmap SQL injection tester
  POST /api/tools/metasploit — msfconsole module runner
  POST /api/tools/hydra      — hydra credential brute-force
  POST /api/tools/john       — john the ripper hash cracker
  POST /api/tools/wpscan     — wpscan WordPress scanner
  POST /api/tools/enum4linux — enum4linux SMB/Samba enumeration

Configuration via environment:
  KALI_MCP_URL     — base URL, default http://kali:8000
  KALI_MCP_TIMEOUT — request timeout in seconds, default 180
"""
import json
import os
import urllib.error
import urllib.request


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _base_url() -> str:
    return os.environ.get("KALI_MCP_URL", "http://kali:8000").rstrip("/")


def _timeout() -> int:
    try:
        return int(os.environ.get("KALI_MCP_TIMEOUT", "180"))
    except (TypeError, ValueError):
        return 180


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class KaliApiError(Exception):
    """The Kali API server returned an error response."""


class KaliUnreachable(KaliApiError):
    """The kali container could not be contacted."""


class KaliToolNotFound(KaliApiError):
    """No endpoint exists for the requested tool name."""


# ---------------------------------------------------------------------------
# Known tools and their REST endpoint paths
# ---------------------------------------------------------------------------

TOOL_ENDPOINTS: dict[str, str] = {
    "nmap":       "/api/tools/nmap",
    "gobuster":   "/api/tools/gobuster",
    "dirb":       "/api/tools/dirb",
    "nikto":      "/api/tools/nikto",
    "sqlmap":     "/api/tools/sqlmap",
    "metasploit": "/api/tools/metasploit",
    "hydra":      "/api/tools/hydra",
    "john":       "/api/tools/john",
    "wpscan":     "/api/tools/wpscan",
    "enum4linux": "/api/tools/enum4linux",
    "command":    "/api/command",
}

TOOL_SCHEMAS: dict[str, dict] = {
    "nmap": {
        "description": "Network port scanner. Discovers open ports, services and OS fingerprints.",
        "properties": {
            "target":          {"type": "string", "description": "IP, hostname or CIDR range to scan."},
            "scan_type":       {"type": "string", "description": "Nmap flags (default: -sCV). Examples: -sS, -sU, -A."},
            "ports":           {"type": "string", "description": "Port range, e.g. '22,80,443' or '1-1000'. Omit for default."},
            "additional_args": {"type": "string", "description": "Extra nmap flags (default: -T4 -Pn)."},
        },
        "required": ["target"],
    },
    "gobuster": {
        "description": "Directory/DNS brute-force tool for web enumeration.",
        "properties": {
            "url":             {"type": "string", "description": "Target URL, e.g. http://target.local/."},
            "mode":            {"type": "string", "description": "Scan mode: dir, dns, fuzz or vhost (default: dir)."},
            "wordlist":        {"type": "string", "description": "Path to wordlist (default: /usr/share/wordlists/dirb/common.txt)."},
            "additional_args": {"type": "string", "description": "Extra gobuster flags."},
        },
        "required": ["url"],
    },
    "dirb": {
        "description": "Web content scanner / directory brute-forcer.",
        "properties": {
            "url":             {"type": "string", "description": "Target URL."},
            "wordlist":        {"type": "string", "description": "Path to wordlist (default: /usr/share/wordlists/dirb/common.txt)."},
            "additional_args": {"type": "string", "description": "Extra dirb flags."},
        },
        "required": ["url"],
    },
    "nikto": {
        "description": "Web server vulnerability scanner.",
        "properties": {
            "target":          {"type": "string", "description": "Target URL or host (e.g. http://target/ or 192.168.1.1)."},
            "additional_args": {"type": "string", "description": "Extra nikto flags."},
        },
        "required": ["target"],
    },
    "sqlmap": {
        "description": "Automatic SQL injection detection and exploitation tool.",
        "properties": {
            "url":             {"type": "string", "description": "Target URL with the vulnerable parameter (e.g. http://target/?id=1)."},
            "data":            {"type": "string", "description": "POST data string if testing a POST request."},
            "additional_args": {"type": "string", "description": "Extra sqlmap flags (e.g. --dbs, --dump, --level=3)."},
        },
        "required": ["url"],
    },
    "metasploit": {
        "description": "Run a Metasploit Framework module via msfconsole.",
        "properties": {
            "module":  {"type": "string", "description": "Module path, e.g. exploit/multi/handler or auxiliary/scanner/portscan/tcp."},
            "options": {"type": "object", "description": "Module options as key-value pairs, e.g. {\"RHOSTS\": \"192.168.1.1\", \"LPORT\": \"4444\"}."},
        },
        "required": ["module"],
    },
    "hydra": {
        "description": "Network login brute-force tool. Supports SSH, FTP, HTTP, SMB and many others.",
        "properties": {
            "target":         {"type": "string", "description": "Target IP or hostname."},
            "service":        {"type": "string", "description": "Service to attack (e.g. ssh, ftp, http-get, smb)."},
            "username":       {"type": "string", "description": "Single username to test."},
            "username_file":  {"type": "string", "description": "Path to file with usernames (one per line)."},
            "password":       {"type": "string", "description": "Single password to test."},
            "password_file":  {"type": "string", "description": "Path to password list (e.g. /usr/share/wordlists/rockyou.txt)."},
            "additional_args": {"type": "string", "description": "Extra hydra flags."},
        },
        "required": ["target", "service"],
    },
    "john": {
        "description": "John the Ripper — offline password/hash cracker.",
        "properties": {
            "hash_file":       {"type": "string", "description": "Path to the file containing hashes to crack."},
            "wordlist":        {"type": "string", "description": "Path to wordlist (default: /usr/share/wordlists/rockyou.txt)."},
            "format":          {"type": "string", "description": "Hash format (e.g. md5crypt, sha256crypt). Leave empty for auto-detect."},
            "additional_args": {"type": "string", "description": "Extra john flags."},
        },
        "required": ["hash_file"],
    },
    "wpscan": {
        "description": "WordPress security scanner.",
        "properties": {
            "url":             {"type": "string", "description": "Target WordPress site URL."},
            "additional_args": {"type": "string", "description": "Extra wpscan flags (e.g. --enumerate u,p for users and plugins)."},
        },
        "required": ["url"],
    },
    "enum4linux": {
        "description": "SMB/Samba/Windows share enumeration tool.",
        "properties": {
            "target":          {"type": "string", "description": "Target IP or hostname."},
            "additional_args": {"type": "string", "description": "Extra enum4linux flags (default: -a for all enumeration)."},
        },
        "required": ["target"],
    },
    "command": {
        "description": (
            "Run any arbitrary shell command inside the Kali container. "
            "Use this for tools not covered by the named endpoints (e.g. masscan, sslscan, curl). "
            "The command runs with a 180-second timeout."
        ),
        "properties": {
            "command": {"type": "string", "description": "Full shell command to execute, e.g. 'sslscan --version 192.168.1.1'."},
        },
        "required": ["command"],
    },
}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _post(endpoint: str, payload: dict) -> dict:
    url = f"{_base_url()}{endpoint}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_timeout()) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        # HTTP 4xx/5xx — server is reachable but returned an error response
        try:
            body_text = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body_text = str(exc)
        raise KaliApiError(
            f"Kali API {exc.code} at {url}: {body_text[:300]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise KaliUnreachable(
            f"Cannot reach kali API at {url}: {exc.reason}"
        ) from exc
    except OSError as exc:
        raise KaliUnreachable(f"Network error contacting kali: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise KaliApiError("Kali API returned non-JSON response") from exc


def _get(endpoint: str) -> dict:
    url = f"{_base_url()}{endpoint}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.URLError as exc:
        raise KaliUnreachable(
            f"Cannot reach kali API at {url}: {exc.reason}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise KaliApiError("Kali API returned non-JSON response") from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def health() -> dict:
    """Return the health check response from the kali container."""
    return _get("/health")


def list_tools() -> list[dict]:
    """Return the list of available Kali tools with their parameter schemas."""
    return [
        {
            "name": name,
            "description": schema["description"],
            "required": schema.get("required", []),
            "parameters": schema.get("properties", {}),
        }
        for name, schema in TOOL_SCHEMAS.items()
    ]


def run_tool(tool_name: str, arguments: dict) -> dict:
    """Execute a Kali tool by name via its REST endpoint.

    tool_name must be one of the keys in TOOL_ENDPOINTS (nmap, gobuster, nikto,
    sqlmap, metasploit, hydra, john, wpscan, enum4linux, dirb, command).

    Returns the API response dict with keys:
      stdout, stderr, return_code, success, timed_out, partial_results
    """
    endpoint = TOOL_ENDPOINTS.get(tool_name)
    if endpoint is None:
        raise KaliToolNotFound(
            f"Unknown tool '{tool_name}'. "
            f"Available: {', '.join(sorted(TOOL_ENDPOINTS))}."
        )
    return _post(endpoint, arguments or {})
