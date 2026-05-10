#!/usr/bin/env python3
"""Workspace tools management utility.

Audits and repairs Autobot workspace tool installations so tool names stay
stable while versions live in each tool manifest.

Policy enforced by default:
- Tool directory/name must not include semantic version suffixes like -v2/-v3.
- Every tool must have a manifest.json and tool.py.
- Every manifest must expose a system version field.
- Deprecated/superseded tool directories can be removed after a dry-run report.
- Existing installations can be migrated by copying a versioned tool into its
  canonical directory and deleting the obsolete versioned directory.

The script is intentionally dependency-free and safe-by-default: it only writes
when --apply is passed. Use --json for CI/catalog sync automation.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

VERSION_SUFFIX_RE = re.compile(r"^(?P<base>.+)-v(?P<num>[0-9]+)$")
SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
DEFAULT_VERSION = "0.1.0"


@dataclass
class Finding:
    severity: str
    code: str
    path: str
    message: str
    fix: Optional[str] = None


@dataclass
class ToolInfo:
    slug: str
    path: str
    manifest_path: str
    tool_path: str
    manifest: Dict[str, Any]
    versioned: bool
    canonical_slug: str
    suffix_version: Optional[int]


def read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        return {"__json_error__": str(exc)}


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def canonicalize_slug(slug: str) -> Tuple[str, Optional[int]]:
    match = VERSION_SUFFIX_RE.match(slug)
    if not match:
        return slug, None
    return match.group("base"), int(match.group("num"))


def discover_tools(tools_dir: Path) -> List[ToolInfo]:
    if not tools_dir.exists():
        return []
    tools: List[ToolInfo] = []
    for path in sorted(p for p in tools_dir.iterdir() if p.is_dir()):
        slug = path.name
        canonical, suffix = canonicalize_slug(slug)
        manifest_path = path / "manifest.json"
        tool_path = path / "tool.py"
        tools.append(
            ToolInfo(
                slug=slug,
                path=str(path),
                manifest_path=str(manifest_path),
                tool_path=str(tool_path),
                manifest=read_json(manifest_path),
                versioned=suffix is not None,
                canonical_slug=canonical,
                suffix_version=suffix,
            )
        )
    return tools


def audit_tools(tools: Iterable[ToolInfo]) -> List[Finding]:
    findings: List[Finding] = []
    seen: Dict[str, List[ToolInfo]] = {}
    for tool in tools:
        seen.setdefault(tool.canonical_slug, []).append(tool)
        if tool.versioned:
            findings.append(Finding(
                "error", "VERSION_IN_NAME", tool.path,
                f"Tool name '{tool.slug}' encodes a version; use '{tool.canonical_slug}' and manifest.version instead.",
                "migrate-versioned"
            ))
        if not Path(tool.tool_path).exists():
            findings.append(Finding("error", "MISSING_TOOL_PY", tool.tool_path, "tool.py is required."))
        if not Path(tool.manifest_path).exists():
            findings.append(Finding("error", "MISSING_MANIFEST", tool.manifest_path, "manifest.json is required.", "create-manifest"))
            continue
        if "__json_error__" in tool.manifest:
            findings.append(Finding("error", "INVALID_MANIFEST_JSON", tool.manifest_path, tool.manifest["__json_error__"]))
            continue
        name = tool.manifest.get("name")
        if name and name != tool.slug:
            findings.append(Finding("warning", "MANIFEST_NAME_MISMATCH", tool.manifest_path, f"manifest.name='{name}' differs from directory '{tool.slug}'.", "sync-manifest-name"))
        version = tool.manifest.get("version")
        if not version:
            findings.append(Finding("error", "MISSING_SYSTEM_VERSION", tool.manifest_path, "manifest.version is required.", "set-version"))
        elif not isinstance(version, str) or not SEMVER_RE.match(version):
            findings.append(Finding("warning", "NON_SEMVER_VERSION", tool.manifest_path, f"manifest.version='{version}' is not semver-like."))
        if tool.manifest.get("deprecated") is True:
            findings.append(Finding("warning", "DEPRECATED_TOOL_VISIBLE", tool.path, "Deprecated tool directory is still installed/visible.", "remove-deprecated"))
        for field in ("supersedes", "replacement"):
            value = tool.manifest.get(field)
            if isinstance(value, str) and VERSION_SUFFIX_RE.search(value):
                findings.append(Finding("warning", "OBSOLETE_REFERENCE", tool.manifest_path, f"manifest.{field} references versioned tool '{value}'.", "remove-obsolete-reference"))
            elif isinstance(value, list):
                bad = [x for x in value if isinstance(x, str) and VERSION_SUFFIX_RE.search(x)]
                if bad:
                    findings.append(Finding("warning", "OBSOLETE_REFERENCE", tool.manifest_path, f"manifest.{field} references versioned tools {bad}.", "remove-obsolete-reference"))
    for canonical, group in seen.items():
        if len(group) > 1 and any(t.versioned for t in group):
            names = ", ".join(t.slug for t in group)
            findings.append(Finding("error", "DUPLICATE_CANONICAL_GROUP", f"tools/{canonical}", f"Multiple implementations for same canonical tool: {names}.", "migrate-versioned"))
    return findings


def choose_best_tool(group: List[ToolInfo]) -> ToolInfo:
    """Choose implementation to keep during repair.

    Preference order:
    1. highest explicit manifest.version if semver-like;
    2. highest suffix number (-v3 beats -v2);
    3. largest tool.py content size as a pragmatic proxy for richer implementation;
    4. canonical base directory for tie-break stability.
    """
    def semver_tuple(version: Any) -> Tuple[int, int, int]:
        if isinstance(version, str):
            m = re.match(r"^([0-9]+)\.([0-9]+)\.([0-9]+)", version)
            if m:
                return tuple(map(int, m.groups()))  # type: ignore[return-value]
        return (0, 0, 0)

    def score(tool: ToolInfo) -> Tuple[Tuple[int, int, int], int, int, int]:
        size = Path(tool.tool_path).stat().st_size if Path(tool.tool_path).exists() else 0
        suffix = tool.suffix_version or 0
        canonical_bonus = 1 if not tool.versioned else 0
        return (semver_tuple(tool.manifest.get("version")), suffix, size, canonical_bonus)

    return sorted(group, key=score, reverse=True)[0]


def next_patch_version(version: Any) -> str:
    if isinstance(version, str):
        m = re.match(r"^([0-9]+)\.([0-9]+)\.([0-9]+)", version)
        if m:
            major, minor, patch = map(int, m.groups())
            return f"{major}.{minor}.{patch + 1}"
    return "1.0.0"


def repair_installation(root: Path, apply: bool = False, remove_obsolete_refs: bool = True) -> List[Finding]:
    tools_dir = root / "tools"
    tools = discover_tools(tools_dir)
    findings = audit_tools(tools)
    actions: List[Finding] = []

    by_canonical: Dict[str, List[ToolInfo]] = {}
    for t in tools:
        by_canonical.setdefault(t.canonical_slug, []).append(t)

    for canonical, group in sorted(by_canonical.items()):
        versioned_members = [t for t in group if t.versioned]
        if not versioned_members:
            continue
        best = choose_best_tool(group)
        canonical_path = tools_dir / canonical
        actions.append(Finding("info", "PLAN_CANONICALIZE", str(canonical_path), f"Keep '{best.slug}' as canonical '{canonical}' and remove {[t.slug for t in versioned_members]}."))
        if apply:
            if best.slug != canonical:
                if canonical_path.exists():
                    backup = tools_dir / f".{canonical}.pre-tool-manager-backup"
                    if backup.exists():
                        shutil.rmtree(backup)
                    shutil.copytree(canonical_path, backup)
                    shutil.rmtree(canonical_path)
                shutil.copytree(Path(best.path), canonical_path)
            manifest_path = canonical_path / "manifest.json"
            manifest = read_json(manifest_path)
            if "__json_error__" in manifest:
                manifest = {}
            manifest["name"] = canonical
            manifest.setdefault("description", f"Workspace tool {canonical}")
            manifest["version"] = next_patch_version(manifest.get("version", DEFAULT_VERSION))
            manifest.pop("deprecated", None)
            manifest.pop("replacement", None)
            if remove_obsolete_refs:
                manifest.pop("supersedes", None)
            manifest.setdefault("x-tool-management", {})
            manifest["x-tool-management"].update({
                "canonical_name": canonical,
                "version_policy": "manifest.version",
                "name_policy": "no version suffix in tool directory/name",
                "managed_by": "scripts/workspace_tools_manager.py",
            })
            write_json(manifest_path, manifest)
            for obsolete in versioned_members:
                obsolete_path = Path(obsolete.path)
                if obsolete_path.exists() and obsolete_path != canonical_path:
                    shutil.rmtree(obsolete_path)

    if apply:
        for tool in discover_tools(tools_dir):
            mp = Path(tool.manifest_path)
            if not mp.exists():
                write_json(mp, {"name": tool.slug, "version": DEFAULT_VERSION, "description": f"Workspace tool {tool.slug}"})
                continue
            manifest = read_json(mp)
            if "__json_error__" in manifest:
                continue
            changed = False
            if manifest.get("name") != tool.slug:
                manifest["name"] = tool.slug
                changed = True
            if not manifest.get("version"):
                manifest["version"] = DEFAULT_VERSION
                changed = True
            if remove_obsolete_refs:
                for key in ("supersedes", "replacement"):
                    value = manifest.get(key)
                    if isinstance(value, str) and VERSION_SUFFIX_RE.search(value):
                        manifest.pop(key, None); changed = True
                    elif isinstance(value, list):
                        filtered = [x for x in value if not (isinstance(x, str) and VERSION_SUFFIX_RE.search(x))]
                        if filtered != value:
                            if filtered:
                                manifest[key] = filtered
                            else:
                                manifest.pop(key, None)
                            changed = True
            if changed:
                write_json(mp, manifest)
    return findings + actions


def scan_references(root: Path, patterns: Optional[List[str]] = None) -> List[Finding]:
    patterns = patterns or [r"\b[a-z0-9][a-z0-9-]*-v[0-9]+\b"]
    regexes = [re.compile(p) for p in patterns]
    findings: List[Finding] = []
    ignored_dirs = {".git", "__pycache__", ".pytest_cache", "node_modules"}
    for path in root.rglob("*"):
        if any(part in ignored_dirs for part in path.parts):
            continue
        if not path.is_file() or path.suffix in {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".zip", ".gz"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for regex in regexes:
            matches = sorted(set(regex.findall(text)))
            if matches:
                findings.append(Finding("warning", "VERSIONED_REFERENCE", str(path), f"Found versioned tool references: {matches}", "update-reference"))
    return findings


def build_report(root: Path, include_refs: bool = True) -> Dict[str, Any]:
    tools = discover_tools(root / "tools")
    findings = audit_tools(tools)
    if include_refs:
        findings.extend(scan_references(root))
    return {
        "tool_count": len(tools),
        "versioned_tool_dirs": [t.slug for t in tools if t.versioned],
        "findings": [asdict(f) for f in findings],
        "ok": not any(f.severity == "error" for f in findings),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Audit and repair Autobot workspace tools.")
    parser.add_argument("--root", default=".", help="Workspace/repository root. Default: current directory.")
    parser.add_argument("--apply", action="store_true", help="Apply repairs. Without this flag the command is dry-run only.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--no-ref-scan", action="store_true", help="Skip scanning docs/skills/tools for versioned references.")
    parser.add_argument("--repair", action="store_true", help="Plan/apply repair of existing installations.")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if args.repair:
        repair_installation(root, apply=args.apply)
    report = build_report(root, include_refs=not args.no_ref_scan)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Tool count: {report['tool_count']}")
        print(f"Versioned tool dirs: {report['versioned_tool_dirs'] or 'none'}")
        for f in report["findings"]:
            print(f"[{f['severity']}] {f['code']} {f['path']}: {f['message']}")
        print("OK" if report["ok"] else "FAILED")
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
