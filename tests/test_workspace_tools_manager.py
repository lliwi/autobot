import json
from pathlib import Path
import importlib.util
import sys

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "workspace_tools_manager.py"
spec = importlib.util.spec_from_file_location("workspace_tools_manager", MODULE_PATH)
manager = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = manager
spec.loader.exec_module(manager)


def write_tool(root: Path, slug: str, version="0.1.0", body="def handler(**kwargs): return {}", **manifest_extra):
    d = root / "tools" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "tool.py").write_text(body, encoding="utf-8")
    manifest = {"name": slug, "version": version, "description": slug}
    manifest.update(manifest_extra)
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return d


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
