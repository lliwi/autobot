"""Secrets scanner for promotion bundles and PRs.

Scans every text file in a tool/skill directory for hardcoded sensitive data
before it leaves the workspace. HIGH findings block the promotion; MEDIUM/LOW
are included as warnings in the PR body and PROMOTION.md.

Each finding:
  {"severity": "high"|"medium"|"low", "file": str, "line": int,
   "pattern_name": str, "snippet": str}
"""
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Pattern registry
# ---------------------------------------------------------------------------

_HIGH = [
    (r"-----BEGIN\s+(?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----",
     "clave privada"),
    (r"ghp_[A-Za-z0-9]{36}",
     "GitHub personal access token"),
    (r"ghs_[A-Za-z0-9]{36}",
     "GitHub app installation token"),
    (r"gho_[A-Za-z0-9]{36}",
     "GitHub OAuth token"),
    (r"AKIA[0-9A-Z]{16}",
     "AWS access key ID"),
    (r"sk-[A-Za-z0-9]{48}",
     "OpenAI API key"),
    (r"AIza[0-9A-Za-z_\-]{35}",
     "Google API key"),
    (r"(?i)(?:password|passwd|pwd)\s*=\s*['\"][^'\"]{6,}['\"]",
     "contraseña hardcodeada"),
    (r"postgresql://[^:@\s]+:[^@\s]+@",
     "PostgreSQL con credenciales en URI"),
    (r"redis://:?[^@\s]+@",
     "Redis con credenciales en URI"),
    (r"mongodb://[^:@\s]+:[^@\s]+@",
     "MongoDB con credenciales en URI"),
    (r"mysql://[^:@\s]+:[^@\s]+@",
     "MySQL con credenciales en URI"),
    (r"(?i)(?:secret_key|api_key|apikey|access_token|auth_token)\s*=\s*['\"][A-Za-z0-9_\-\.]{16,}['\"]",
     "secreto/API key hardcodeado"),
]

_MEDIUM = [
    (r"Bearer\s+[A-Za-z0-9_\-\.]{20,}",
     "Bearer token hardcodeado"),
    (r"(?i)(?:authorization|x-api-key)\s*[:=]\s*['\"][^'\"]{10,}['\"]",
     "cabecera de autenticación hardcodeada"),
    # Public IPv4 — private ranges are filtered out after match
    (r"\b(?:25[0-5]|2[0-4]\d|[01]?\d\d?)(?:\.(?:25[0-5]|2[0-4]\d|[01]?\d\d?)){3}\b",
     "dirección IP"),
]

_LOW = [
    (r"(?i)#\s*TODO[:\s]+.*(?:password|token|secret|key|credential)",
     "TODO con referencia a credencial"),
]

_COMPILED_HIGH = [(re.compile(p), name) for p, name in _HIGH]
_COMPILED_MEDIUM = [(re.compile(p), name) for p, name in _MEDIUM]
_COMPILED_LOW = [(re.compile(p), name) for p, name in _LOW]

# Private / link-local / loopback IP ranges that are safe in code
_PRIVATE_IP = re.compile(
    r"^(?:127\.|0\.0\.0\.0|10\.\d|192\.168\.|169\.254\.|"
    r"172\.(?:1[6-9]|2\d|3[01])\.|255\.)"
)

# Text file extensions to scan
_TEXT_EXTS = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".sh", ".env"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_directory(source_dir: Path) -> dict:
    """Scan all text files in source_dir for sensitive data.

    Returns:
        {
            "ok": bool,           # False if any HIGH findings
            "findings": [...],
            "summary": str,
        }
    """
    findings = []
    for fpath in sorted(source_dir.rglob("*")):
        if not fpath.is_file():
            continue
        if fpath.suffix.lower() not in _TEXT_EXTS:
            continue
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel_name = str(fpath.relative_to(source_dir))
        findings.extend(_scan_text(text, rel_name))

    high = [f for f in findings if f["severity"] == "high"]
    medium = [f for f in findings if f["severity"] == "medium"]
    low = [f for f in findings if f["severity"] == "low"]

    parts = []
    if high:
        parts.append(f"{len(high)} HIGH")
    if medium:
        parts.append(f"{len(medium)} MEDIUM")
    if low:
        parts.append(f"{len(low)} LOW")
    summary = ("Sin problemas detectados." if not findings
               else f"{len(findings)} hallazgo(s): " + ", ".join(parts))

    return {
        "ok": len(high) == 0,
        "findings": findings,
        "summary": summary,
    }


def findings_to_markdown(findings: list) -> str:
    """Format scan findings as a Markdown table for inclusion in PROMOTION.md / PR body."""
    if not findings:
        return "_Ningún problema de seguridad detectado._\n"

    lines = [
        "| Severidad | Fichero | Línea | Problema | Extracto |",
        "|---|---|---|---|---|",
    ]
    for f in findings:
        sev = f["severity"].upper()
        emoji = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🔵"}.get(sev, "")
        lines.append(
            f"| {emoji} {sev} | `{f['file']}` | {f['line']} "
            f"| {f['pattern_name']} | `{f['snippet']}` |"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _scan_text(text: str, filename: str) -> list:
    findings = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for pattern, name in _COMPILED_HIGH:
            m = pattern.search(line)
            if m:
                findings.append(_make_finding("high", filename, lineno, name, line, m))

        for pattern, name in _COMPILED_MEDIUM:
            m = pattern.search(line)
            if m:
                # Filter private/loopback IPs
                if name == "dirección IP" and _PRIVATE_IP.match(m.group()):
                    continue
                findings.append(_make_finding("medium", filename, lineno, name, line, m))

        for pattern, name in _COMPILED_LOW:
            m = pattern.search(line)
            if m:
                findings.append(_make_finding("low", filename, lineno, name, line, m))

    return findings


def _make_finding(severity: str, filename: str, lineno: int,
                  pattern_name: str, line: str, match: re.Match) -> dict:
    snippet = _mask_snippet(line.strip(), match)
    return {
        "severity": severity,
        "file": filename,
        "line": lineno,
        "pattern_name": pattern_name,
        "snippet": snippet[:120],
    }


def _mask_snippet(line: str, match: re.Match) -> str:
    """Replace the matched value with asterisks, keeping context."""
    start, end = match.start(), match.end()
    matched = line[start:end]
    # Keep first 4 chars of the match, mask the rest
    visible = matched[:4]
    masked = visible + "*" * min(len(matched) - 4, 12)
    return line[:start] + masked + line[end:]
