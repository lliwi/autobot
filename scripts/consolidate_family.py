"""One-off helper to consolidate tool families into generic dispatcher tools.

Each consolidated tool keeps every original implementation VERBATIM under
``ops/<action>/`` and loads it on demand by file path (the same mechanism the
subprocess runner uses), so behaviour is preserved exactly — only the slug
namespace is unified behind an ``action`` discriminator.

Run inside the web container with an app context:
    docker compose run --rm --no-deps web python scripts/consolidate_family.py
"""
import json
import shutil
import sys
from pathlib import Path


DISPATCHER_TEMPLATE = '''"""Generic {family} tool — dispatches to one of several operations.

{actions_doc}

Call with ``action=<name>`` plus that operation's own parameters. Each
operation preserves the behaviour of the original standalone tool.
"""
import importlib.util
import os
import sys

_OPS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ops")
_ACTIONS = {actions_list}


def _load(action):
    op_py = os.path.join(_OPS, action, "tool.py")
    if not os.path.isfile(op_py):
        return None
    op_dir = os.path.dirname(op_py)
    if op_dir not in sys.path:
        sys.path.insert(0, op_dir)
    spec = importlib.util.spec_from_file_location("op_" + action.replace("-", "_"), op_py)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, "handler", None)


def handler(_agent=None, action=None, **kwargs):
    if not action:
        return {{"error": "param 'action' is required", "available_actions": _ACTIONS}}
    if action not in _ACTIONS:
        return {{"error": f"unknown action '{{action}}'", "available_actions": _ACTIONS}}
    fn = _load(action)
    if fn is None:
        return {{"error": f"operation '{{action}}' could not be loaded"}}
    return fn(_agent=_agent, **kwargs)
'''


def consolidate(new_slug, family_label, members, description):
    """members: list of (action, old_slug). Returns dict report."""
    from app.extensions import db
    from app.models.tool import AgentTool, Tool
    from app.workspace.manager import get_global_tools_path

    gt = get_global_tools_path()
    new_dir = gt / new_slug
    ops_dir = new_dir / "ops"

    # Fresh build of the new tool dir
    if new_dir.exists():
        shutil.rmtree(new_dir)
    ops_dir.mkdir(parents=True)

    action_descs = {}
    merged_reqs = set()
    union_params = {}
    for action, old in members:
        src = gt / old
        if not src.exists():
            print(f"  WARN source missing: {old}")
            continue
        shutil.copytree(src, ops_dir / action)
        # collect manifest info
        man = src / "manifest.json"
        if man.exists():
            try:
                m = json.loads(man.read_text())
                action_descs[action] = (m.get("description") or "").strip()
                for k, v in (m.get("parameters", {}).get("properties", {}) or {}).items():
                    union_params.setdefault(k, v)
            except Exception:
                action_descs[action] = ""
        req = src / "requirements.txt"
        if req.exists():
            for line in req.read_text().splitlines():
                s = line.strip()
                if s and not s.startswith("#"):
                    merged_reqs.add(s)

    actions = [a for a, _ in members]
    actions_doc = "\n".join(f"- {a}: {action_descs.get(a, '')}" for a in actions)
    tool_py = DISPATCHER_TEMPLATE.format(
        family=family_label,
        actions_doc=actions_doc,
        actions_list=json.dumps(actions),
    )
    (new_dir / "tool.py").write_text(tool_py, encoding="utf-8")

    # manifest: action enum (required) + union of members' params (all optional)
    props = {"action": {"type": "string", "enum": actions,
                        "description": "Which operation to run. " +
                        "; ".join(f"{a}={action_descs.get(a,'')[:60]}" for a in actions)}}
    for k, v in union_params.items():
        if k != "action":
            props[k] = v
    manifest = {
        "name": new_slug,
        "description": description,
        "version": "1.0.0",
        "parameters": {"type": "object", "properties": props, "required": ["action"]},
        "timeout": 60,
    }
    (new_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if merged_reqs:
        (new_dir / "requirements.txt").write_text("\n".join(sorted(merged_reqs)) + "\n", encoding="utf-8")

    # --- DB: create new Tool, migrate assignments, delete old tools ---
    old_slugs = [old for _, old in members]
    old_tools = Tool.query.filter(Tool.slug.in_(old_slugs)).all()
    agent_ids = set()
    for ot in old_tools:
        for at in AgentTool.query.filter_by(tool_id=ot.id).all():
            agent_ids.add(at.agent_id)

    new_tool = Tool.query.filter_by(slug=new_slug).first()
    if new_tool is None:
        new_tool = Tool(slug=new_slug)
        db.session.add(new_tool)
    new_tool.name = new_slug
    new_tool.description = description
    new_tool.version = manifest["version"]
    new_tool.source = "workspace"
    new_tool.manifest_json = manifest
    new_tool.path = f"tools/{new_slug}"
    new_tool.timeout = 60
    db.session.flush()

    for aid in agent_ids:
        if not AgentTool.query.filter_by(tool_id=new_tool.id, agent_id=aid).first():
            db.session.add(AgentTool(tool_id=new_tool.id, agent_id=aid, enabled=True))

    for ot in old_tools:
        db.session.delete(ot)  # cascade removes its AgentTool rows
    for old in old_slugs:
        d = gt / old
        if d.exists():
            shutil.rmtree(d)

    db.session.commit()
    return {"new": new_slug, "members": len(members), "agents": len(agent_ids)}


def rename(old_slug, new_slug):
    from app.extensions import db
    from app.models.tool import Tool
    from app.workspace.manager import get_global_tools_path
    gt = get_global_tools_path()
    t = Tool.query.filter_by(slug=old_slug).first()
    if t is None:
        print(f"  WARN rename: {old_slug} not found")
        return
    (gt / old_slug).rename(gt / new_slug)
    man = gt / new_slug / "manifest.json"
    if man.exists():
        m = json.loads(man.read_text())
        m["name"] = new_slug
        man.write_text(json.dumps(m, indent=2) + "\n", encoding="utf-8")
        t.manifest_json = m
    t.slug = new_slug
    t.name = new_slug
    t.path = f"tools/{new_slug}"
    db.session.commit()
    print(f"  renamed {old_slug} -> {new_slug}")


# --------------------------------------------------------------------------
# Family specs
# --------------------------------------------------------------------------

FAMILIES = [
    ("github", "GitHub", [
        ("issue-create", "github-issue-creator"),
        ("issue-comment", "github-issue-commenter"),
        ("pr-create", "github-pr-creator-token"),
    ], "GitHub operations: create issues, comment on issues, and open pull requests."),

    ("portainer", "Portainer", [
        ("containers-list", "portainer-containers-agentcred"),
        ("container-logs", "portainer-container-logs-token"),
        ("container-restart", "portainer-container-restart-token"),
    ], "Portainer container operations: list containers, fetch logs, restart a container."),

    ("homeassistant", "Home Assistant", [
        ("assist", "homeassistant-assist"),
        ("entity-search", "homeassistant-entity-search-token"),
    ], "Home Assistant: run an Assist conversation command or search entities."),

    ("jackett", "Jackett/Transmission", [
        ("search", "jackett-search-token"),
        ("transmission-add", "jackett-transmission-add-token"),
        ("transmission-add-metainfo", "jackett-transmission-add-metainfo-token"),
    ], "Jackett torrent search and adding results to Transmission."),

    ("terramaster", "TerraMaster NAS", [
        ("series-rename", "terramaster-series-renamer"),
        ("smb-rename", "terramaster-smb-kali-renamer"),
        ("smb-list", "terramaster-smb-lister"),
        ("space", "terramaster-space-agentcred"),
    ], "TerraMaster NAS over SMB: rename series/files, list shares, report free space."),

    ("cloudflare-read", "Cloudflare (read)", [
        ("zones", "cloudflare-zones-reader-token"),
        ("zone-settings", "cloudflare-zone-settings-reader-token"),
        ("firewall", "cloudflare-firewall-reader-token"),
        ("tunnel-config", "cloudflare-tunnel-config-reader-token"),
        ("tunnels-by-zone", "cloudflare-tunnels-by-zone-account-token"),
        ("zerotrust", "cloudflare-zerotrust-reader"),
    ], "Read-only Cloudflare lookups: zones, zone settings, firewall, tunnels, Zero Trust."),

    ("notion-publish", "Notion (publish)", [
        ("page", "notion-publisher"),
        ("subpage", "notion-subpage-publisher"),
        ("native-page", "notion-native-pages"),
        ("native-remodel", "notion-native-remodel-token"),
        ("page-publisher", "notion-page-publisher-agentcred"),
        ("osint-audit", "notion-osint-audit-publisher"),
        ("osint-audit-native", "notion-osint-audit-native-publisher"),
        ("osint-audit-safe", "notion-osint-audit-publish-safe"),
        ("nomasceros-audit", "notion-nomasceros-audit-publisher"),
        ("fb-audit-update", "notion-fb-audit-updater"),
        ("weekly-report", "notion-system-weekly-report-publisher"),
        ("weekly-report-native", "notion-weekly-system-report-native"),
    ], "Publish/create Notion pages and reports (multiple publishing variants)."),

    ("notion-audit", "Notion (audit/restyle)", [
        ("content-restyle", "notion-audit-content-restyler-token"),
        ("style-apply", "notion-audit-style-applier-token"),
        ("style-rewrite", "notion-page-style-rewriter-token"),
        ("fb-audit-native", "notion-fb-audit-native-safe"),
        ("skill-doc-audit", "notion-skill-doc-auditor-token"),
    ], "Audit and restyle existing Notion pages."),

    ("notion-read", "Notion (read)", [
        ("page-search", "notion-page-search-token"),
        ("blocks-list", "notion-blocks-lister-token"),
        ("block-inspect", "notion-page-block-inspector"),
    ], "Read-only Notion lookups: search pages, list/inspect blocks."),
]

RENAMES = [
    ("cloudflare-csp-updater-token", "cloudflare-csp-updater"),
]


def main():
    import app as a
    ap = a.create_app()
    with ap.app_context():
        from app.models.tool import Tool
        before = Tool.query.count()
        for spec in FAMILIES:
            r = consolidate(*spec)
            print(f"  ✓ {r['new']:20} from {r['members']} tools, {r['agents']} agents")
        for old, new in RENAMES:
            rename(old, new)
        after = Tool.query.count()
        print(f"\n  tools: {before} -> {after}")


if __name__ == "__main__":
    sys.exit(main())
