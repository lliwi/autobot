"""Kali Linux security tooling, dispatched to the kali container's REST API.

All of these operate against a separate Kali container with internet + LAN
access. Authorization is the caller's responsibility — the descriptions repeat
the "explicit authorization only" warning the model must respect.
"""
from app.runtime.tool_registry.core import ToolDefinition, register


def register_kali_tools():
    register(
        ToolDefinition(
            name="kali_nmap",
            description=(
                "Run an nmap port scan against a target. The Kali container has internet and LAN access. "
                "IMPORTANT: only scan targets you have explicit authorization to test."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "IP address, hostname, or CIDR range to scan (e.g. 192.168.1.1 or 10.0.0.0/24)."},
                    "ports":  {"type": "string", "description": "Port range or list, e.g. '22,80,443' or '1-1000'. Omit to use nmap default."},
                    "scan_type": {"type": "string", "description": "Nmap scan flags (default: -sCV). Use -sT for TCP connect, -sS for SYN, -A for aggressive."},
                    "additional_args": {"type": "string", "description": "Extra nmap flags (default: -T4 -Pn). Example: --script vuln"},
                },
                "required": ["target"],
            },
            handler=lambda **kwargs: _kali_dispatch("nmap", **kwargs),
        )
    )

    register(
        ToolDefinition(
            name="kali_nikto",
            description=(
                "Run a nikto web server vulnerability scan. "
                "IMPORTANT: only scan targets you have explicit authorization to test."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Target URL or host, e.g. http://192.168.1.1/ or 192.168.1.1."},
                    "additional_args": {"type": "string", "description": "Extra nikto flags, e.g. -ssl to force HTTPS."},
                },
                "required": ["target"],
            },
            handler=lambda **kwargs: _kali_dispatch("nikto", **kwargs),
        )
    )

    register(
        ToolDefinition(
            name="kali_gobuster",
            description=(
                "Run gobuster to brute-force directories, DNS subdomains or virtual hosts. "
                "IMPORTANT: only scan targets you have explicit authorization to test."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url":      {"type": "string", "description": "Target URL, e.g. http://192.168.1.1/."},
                    "mode":     {"type": "string", "description": "Scan mode: dir (default), dns, fuzz, or vhost."},
                    "wordlist": {"type": "string", "description": "Path to wordlist (default: /usr/share/wordlists/dirb/common.txt)."},
                    "additional_args": {"type": "string", "description": "Extra gobuster flags."},
                },
                "required": ["url"],
            },
            handler=lambda **kwargs: _kali_dispatch("gobuster", **kwargs),
        )
    )

    register(
        ToolDefinition(
            name="kali_sqlmap",
            description=(
                "Run sqlmap to detect and exploit SQL injection vulnerabilities. "
                "IMPORTANT: only test targets you have explicit authorization to test."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url":  {"type": "string", "description": "Target URL with the injectable parameter, e.g. http://host/?id=1."},
                    "data": {"type": "string", "description": "POST body data if testing a POST endpoint."},
                    "additional_args": {"type": "string", "description": "Extra sqlmap flags, e.g. --dbs --level=3 --dump."},
                },
                "required": ["url"],
            },
            handler=lambda **kwargs: _kali_dispatch("sqlmap", **kwargs),
        )
    )

    register(
        ToolDefinition(
            name="kali_dirb",
            description=(
                "Run dirb to brute-force web content (directories and files). "
                "IMPORTANT: only scan targets you have explicit authorization to test."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url":             {"type": "string", "description": "Target URL, e.g. http://192.168.1.1/."},
                    "wordlist":        {"type": "string", "description": "Path to wordlist (default: /usr/share/wordlists/dirb/common.txt)."},
                    "additional_args": {"type": "string", "description": "Extra dirb flags."},
                },
                "required": ["url"],
            },
            handler=lambda **kwargs: _kali_dispatch("dirb", **kwargs),
        )
    )

    register(
        ToolDefinition(
            name="kali_hydra",
            description=(
                "Run hydra to brute-force network login credentials (SSH, FTP, HTTP, SMB, RDP, etc.). "
                "IMPORTANT: only use against targets with explicit written authorization."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target":          {"type": "string", "description": "Target IP or hostname."},
                    "service":         {"type": "string", "description": "Service protocol: ssh, ftp, http-get, http-post-form, smb, rdp, telnet, mysql, etc."},
                    "username":        {"type": "string", "description": "Single username to test."},
                    "username_file":   {"type": "string", "description": "Path to username list file."},
                    "password":        {"type": "string", "description": "Single password to test."},
                    "password_file":   {"type": "string", "description": "Path to password list (e.g. /usr/share/wordlists/rockyou.txt)."},
                    "additional_args": {"type": "string", "description": "Extra hydra flags."},
                },
                "required": ["target", "service"],
            },
            handler=lambda **kwargs: _kali_dispatch("hydra", **kwargs),
        )
    )

    register(
        ToolDefinition(
            name="kali_metasploit",
            description=(
                "Run a Metasploit Framework module via msfconsole. "
                "IMPORTANT: only use against targets with explicit written authorization."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "module":  {"type": "string", "description": "Module path, e.g. auxiliary/scanner/portscan/tcp or exploit/multi/handler."},
                    "options": {"type": "object", "description": "Module options as key-value pairs, e.g. {\"RHOSTS\": \"192.168.1.1\", \"LPORT\": \"4444\"}."},
                },
                "required": ["module"],
            },
            handler=lambda **kwargs: _kali_dispatch("metasploit", **kwargs),
        )
    )

    register(
        ToolDefinition(
            name="kali_john",
            description="Run John the Ripper to crack password hashes offline.",
            parameters={
                "type": "object",
                "properties": {
                    "hash_file":       {"type": "string", "description": "Path to the file containing hashes to crack (must be inside the kali container or a known path)."},
                    "wordlist":        {"type": "string", "description": "Path to wordlist (default: /usr/share/wordlists/rockyou.txt)."},
                    "format":          {"type": "string", "description": "Hash format, e.g. md5crypt, sha256crypt, ntlm. Leave empty for auto-detect."},
                    "additional_args": {"type": "string", "description": "Extra john flags."},
                },
                "required": ["hash_file"],
            },
            handler=lambda **kwargs: _kali_dispatch("john", **kwargs),
        )
    )

    register(
        ToolDefinition(
            name="kali_wpscan",
            description=(
                "Run wpscan to audit WordPress installations (plugins, themes, users, vulnerabilities). "
                "IMPORTANT: only scan targets you have explicit authorization to test."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url":             {"type": "string", "description": "Target WordPress site URL, e.g. http://target/."},
                    "additional_args": {"type": "string", "description": "Extra wpscan flags, e.g. --enumerate u,p to list users and plugins."},
                },
                "required": ["url"],
            },
            handler=lambda **kwargs: _kali_dispatch("wpscan", **kwargs),
        )
    )

    register(
        ToolDefinition(
            name="kali_enum4linux",
            description=(
                "Run enum4linux to enumerate SMB/Samba shares, users, groups and OS info on Windows/Linux hosts. "
                "IMPORTANT: only scan targets you have explicit authorization to test."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target":          {"type": "string", "description": "Target IP or hostname."},
                    "additional_args": {"type": "string", "description": "Extra enum4linux flags (default: -a for full enumeration)."},
                },
                "required": ["target"],
            },
            handler=lambda **kwargs: _kali_dispatch("enum4linux", **kwargs),
        )
    )

    register(
        ToolDefinition(
            name="kali_command",
            description=(
                "Run any arbitrary shell command inside the Kali Linux container (internet + LAN access). "
                "Use this for tools not covered by the named kali_* tools: "
                "sslscan, masscan, netcat, curl, dnsenum, whatweb, etc. "
                "IMPORTANT: only use against targets with explicit written authorization."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Full shell command to run, e.g. 'sslscan 192.168.1.1' or 'whatweb http://target/'."},
                },
                "required": ["command"],
            },
            handler=lambda **kwargs: _kali_dispatch("command", **kwargs),
        )
    )


def _kali_dispatch(tool_name: str, **kwargs) -> dict:
    """Call the kali REST API for a named tool using explicit keyword arguments."""
    # Strip internal kwargs that come from the tool executor
    args = {k: v for k, v in kwargs.items() if not k.startswith("_") and v is not None and v != ""}

    try:
        from app.services.kali_mcp_client import KaliApiError, KaliToolNotFound, KaliUnreachable, run_tool
    except ImportError as exc:
        return {"error": f"kali_mcp_client import failed: {exc}"}

    try:
        result = run_tool(tool_name, args)
    except KaliUnreachable as exc:
        return {
            "error": "Kali container not reachable.",
            "detail": str(exc),
            "hint": "Check: docker compose ps kali — and KALI_MCP_URL in .env (default: http://kali:8000).",
        }
    except KaliToolNotFound:
        return {"error": f"Tool '{tool_name}' not found on the Kali API."}
    except KaliApiError as exc:
        return {"error": f"Kali API error: {exc}"}
    except Exception as exc:
        return {"error": f"kali_{tool_name} failed: {exc}"}

    stdout = result.get("stdout", "").strip()
    stderr = result.get("stderr", "").strip()
    output = stdout
    if stderr:
        output = f"{stdout}\n--- stderr ---\n{stderr}".strip()

    return {
        "tool": tool_name,
        "success": result.get("success", False),
        "timed_out": result.get("timed_out", False),
        "return_code": result.get("return_code"),
        "output": output or "(no output)",
    }
