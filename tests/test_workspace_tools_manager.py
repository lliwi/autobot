"""Tests for scripts/workspace_tools_manager.py.

Covers both the original 5 cases and the 8 new cases added during robustness review.
Uses only tmp_path — no Flask app or DB needed.
"""
import json
import sys
from pathlib import Path

import importlib.util
import pytest

# Load module directly from scripts/ (not on sys.path by default)
_MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "workspace_tools_manager.py"
_spec = importlib.util.spec_from_file_location("workspace_tools_manager", _MODULE_PATH)
manager = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = manager
_spec.loader.exec_module(manager)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_tool(root: Path, slug: str, version="0.1.0", body="def handler(**kwargs): return {}", **manifest_extra):
    d = root / "tools" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "tool.py").write_text(body, encoding="utf-8")
    manifest = {"name": slug, "version": version, "description": slug}
    manifest.update(manifest_extra)
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# Original tests (preserved from PR #20)
# ---------------------------------------------------------------------------

def test_audit_flags_version_in_name(tmp_path):
    write_tool(tmp_path, "cloudflare-csp-updater-token-v3")
    report = manager.build_report(tmp_path, include_refs=False)
    codes = {f["code"] for f in report["findings"]}
    assert "VERSION_IN_NAME" in codes
    assert report["ok"] is False


def test_repair_migrates_highest_version_to_canonical_and_removes_obsolete(tmp_path):
    write_tool(tmp_path, "demo-tool", version="0.1.0", body="def handler(**kwargs): return {'old': True}")
    write_tool(tmp_path, "demo-tool-v2", version="0.2.0", body="def handler(**kwargs): return {'new': True}")
    manager.repair_installation(tmp_path, apply=True)
    assert (tmp_path / "tools" / "demo-tool").exists()
    assert not (tmp_path / "tools" / "demo-tool-v2").exists()
    assert "new" in (tmp_path / "tools" / "demo-tool" / "tool.py").read_text()
    manifest = json.loads((tmp_path / "tools" / "demo-tool" / "manifest.json").read_text())
    assert manifest["name"] == "demo-tool"
    assert manifest["version"] == "0.2.1"
    assert manifest["x-tool-management"]["version_policy"] == "manifest.version"


def test_single_versioned_tool_is_renamed_to_base(tmp_path):
    write_tool(tmp_path, "notion-audit-style-applier-token-v2", version="1.0.0")
    manager.repair_installation(tmp_path, apply=True)
    assert (tmp_path / "tools" / "notion-audit-style-applier-token").exists()
    assert not (tmp_path / "tools" / "notion-audit-style-applier-token-v2").exists()


def test_scan_references_finds_docs_and_skills(tmp_path):
    (tmp_path / "skills" / "x").mkdir(parents=True)
    (tmp_path / "skills" / "x" / "SKILL.md").write_text("Use homeassistant-assist-v2", encoding="utf-8")
    findings = manager.scan_references(tmp_path)
    assert any(f.code == "VERSIONED_REFERENCE" for f in findings)


def test_repair_removes_obsolete_manifest_references(tmp_path):
    write_tool(tmp_path, "demo-tool", version="1.0.0", supersedes=["demo-tool-v2"])
    manager.repair_installation(tmp_path, apply=True)
    manifest = json.loads((tmp_path / "tools" / "demo-tool" / "manifest.json").read_text())
    assert "supersedes" not in manifest


# ---------------------------------------------------------------------------
# New tests — bug fixes (PR #20 robustness review)
# ---------------------------------------------------------------------------

def test_discover_tools_skips_backup_dirs(tmp_path):
    """Backup dirs created during repair (starting with .) must not appear as tools."""
    write_tool(tmp_path, "demo-tool")
    # Simulate a backup directory left by repair
    backup = tmp_path / "tools" / ".demo-tool.pre-tool-manager-backup"
    backup.mkdir(parents=True)
    (backup / "manifest.json").write_text(json.dumps({"name": "demo-tool", "version": "0.0.9"}))
    tools = manager.discover_tools(tmp_path / "tools")
    slugs = [t.slug for t in tools]
    assert "demo-tool" in slugs
    assert ".demo-tool.pre-tool-manager-backup" not in slugs
    assert len(slugs) == 1


def test_apply_without_repair_raises_error(tmp_path):
    """--apply without --repair must produce an error, not silently do nothing."""
    with pytest.raises(SystemExit) as exc_info:
        manager.main(["--root", str(tmp_path), "--apply"])
    assert exc_info.value.code != 0


def test_canonical_bonus_wins_tiebreak(tmp_path):
    """When semver versions are equal, the canonical dir (no suffix) must be kept."""
    # Both have same version 0.1.0 — canonical_bonus should decide
    write_tool(tmp_path, "my-tool", version="0.1.0", body="canonical")
    write_tool(tmp_path, "my-tool-v2", version="0.1.0", body="versioned")
    manager.repair_installation(tmp_path, apply=True)
    content = (tmp_path / "tools" / "my-tool" / "tool.py").read_text()
    # The canonical dir body should win (canonical_bonus=1 > canonical_bonus=0)
    assert "canonical" in content
    assert not (tmp_path / "tools" / "my-tool-v2").exists()


def test_scan_skips_lock_toml_venv_and_patches(tmp_path):
    """scan_references must not produce findings from ignored file types or directories."""
    (tmp_path / "tools").mkdir()
    # Ignored file extensions/names
    (tmp_path / "poetry.lock").write_text("some-package-v3 = {version = ...}", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('requires = ["tool-v2"]', encoding="utf-8")
    (tmp_path / "package-lock.json").write_text('{"dep-v4": {}}', encoding="utf-8")
    # .venv — third-party packages produce many false positives (e.g. pydantic, paramiko)
    venv_pkg = tmp_path / ".venv" / "lib" / "python3.11" / "site-packages" / "some_pkg"
    venv_pkg.mkdir(parents=True)
    (venv_pkg / "module.py").write_text("SCHEMA = 'arguments-v3'", encoding="utf-8")
    # patches/ — historical records, not actionable
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "20260101_patch__tool-v2__manifest.json").write_text('{"name": "tool-v2"}', encoding="utf-8")
    findings = manager.scan_references(tmp_path)
    assert not any(f.code == "VERSIONED_REFERENCE" for f in findings)


def test_dry_run_does_not_modify_files(tmp_path):
    """repair_installation(apply=False) must not touch the filesystem."""
    write_tool(tmp_path, "demo-tool-v2", version="0.5.0")
    original_mtime = (tmp_path / "tools" / "demo-tool-v2" / "manifest.json").stat().st_mtime
    manager.repair_installation(tmp_path, apply=False)
    # Versioned dir still exists, nothing renamed
    assert (tmp_path / "tools" / "demo-tool-v2").exists()
    assert not (tmp_path / "tools" / "demo-tool").exists()
    current_mtime = (tmp_path / "tools" / "demo-tool-v2" / "manifest.json").stat().st_mtime
    assert current_mtime == original_mtime


def test_empty_tools_dir(tmp_path):
    """An empty tools/ directory should produce ok=True with tool_count=0."""
    (tmp_path / "tools").mkdir()
    report = manager.build_report(tmp_path, include_refs=False)
    assert report["ok"] is True
    assert report["tool_count"] == 0
    assert report["findings"] == []


def test_missing_tool_py_flagged(tmp_path):
    """A tool directory without tool.py must produce a MISSING_TOOL_PY finding."""
    d = tmp_path / "tools" / "my-tool"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"name": "my-tool", "version": "0.1.0", "description": "x"}))
    # No tool.py
    report = manager.build_report(tmp_path, include_refs=False)
    codes = {f["code"] for f in report["findings"]}
    assert "MISSING_TOOL_PY" in codes
    assert report["ok"] is False


def test_next_patch_version_fallback(tmp_path):
    """Non-semver version should fall back to DEFAULT_VERSION, not jump to 1.0.0."""
    result = manager.next_patch_version("not-a-version")
    assert result == manager.DEFAULT_VERSION
    assert result != "1.0.0"

    result_missing = manager.next_patch_version(None)
    assert result_missing == manager.DEFAULT_VERSION


# ---------------------------------------------------------------------------
# Bare number suffix pattern (runner2, direct3)
# ---------------------------------------------------------------------------

def test_bare_suffix_flagged_when_sibling_exists(tmp_path):
    """foo2 must be flagged as VERSION_IN_NAME when foo also exists."""
    write_tool(tmp_path, "jackett-transmission-runner")
    write_tool(tmp_path, "jackett-transmission-runner2")
    report = manager.build_report(tmp_path, include_refs=False)
    codes = {f["code"] for f in report["findings"]}
    assert "VERSION_IN_NAME" in codes
    assert report["ok"] is False
    vn_dirs = report["versioned_tool_dirs"]
    assert "jackett-transmission-runner2" in vn_dirs
    assert "jackett-transmission-runner" not in vn_dirs


def test_bare_suffix_not_flagged_without_sibling(tmp_path):
    """foo2 must NOT be flagged when foo does not exist (no sibling → not a version)."""
    write_tool(tmp_path, "notion-api-direct2")
    report = manager.build_report(tmp_path, include_refs=False)
    # Without a sibling 'notion-api-direct', this is treated as canonical
    assert "notion-api-direct2" not in report["versioned_tool_dirs"]


def test_repair_merges_bare_suffix_tools(tmp_path):
    """repair --apply must consolidate runner2 into runner when both exist."""
    write_tool(tmp_path, "runner", version="0.1.0", body="old")
    write_tool(tmp_path, "runner2", version="0.2.0", body="new")
    manager.repair_installation(tmp_path, apply=True)
    assert (tmp_path / "tools" / "runner").exists()
    assert not (tmp_path / "tools" / "runner2").exists()
    content = (tmp_path / "tools" / "runner" / "tool.py").read_text()
    assert "new" in content


def test_sha256_not_flagged_as_version(tmp_path):
    """sha256 alone must not be treated as sha + version 256 (no sibling 'sha' exists)."""
    write_tool(tmp_path, "sha256")
    report = manager.build_report(tmp_path, include_refs=False)
    assert "sha256" not in report["versioned_tool_dirs"]
