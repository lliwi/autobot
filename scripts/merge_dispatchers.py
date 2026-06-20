"""Merge several tools (dispatchers and/or leaf tools) into one generic
dispatcher tool, preserving every operation's implementation verbatim.

Run inside the web container:
    docker compose run --rm --no-deps web python scripts/merge_dispatchers.py
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


def _op_meta(op_dir):
    """Return (description, params_props) from an op dir's manifest.json."""
    man = op_dir / "manifest.json"
    if man.exists():
        try:
            m = json.loads(man.read_text())
            return (m.get("description") or "").strip(), (m.get("parameters", {}).get("properties", {}) or {})
        except Exception:
            pass
    return "", {}


def merge(new_slug, family_label, sources, description):
    """sources: list of dicts:
        {"type": "dispatcher", "slug": "..."}            -> import all its ops/*
        {"type": "leaf", "slug": "...", "action": "..."} -> import as one action
    """
    from app.extensions import db
    from app.models.tool import AgentTool, Tool
    from app.workspace.manager import get_global_tools_path

    gt = get_global_tools_path()
    new_dir = gt / new_slug
    ops_dir = new_dir / "ops"

    # Build into a temp dir first so we never clobber a source mid-merge
    tmp_dir = gt / (new_slug + "__building")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    (tmp_dir / "ops").mkdir(parents=True)

    actions = []
    action_descs = {}
    union_params = {}
    merged_reqs = set()
    source_slugs = []

    def _add_action(action, src_dir):
        if action in action_descs:
            print(f"    WARN duplicate action '{action}' — skipping second")
            return
        shutil.copytree(src_dir, tmp_dir / "ops" / action)
        actions.append(action)
        desc, props = _op_meta(src_dir)
        action_descs[action] = desc
        for k, v in props.items():
            if k != "action":
                union_params.setdefault(k, v)
        req = src_dir / "requirements.txt"
        if req.exists():
            for line in req.read_text().splitlines():
                s = line.strip()
                if s and not s.startswith("#"):
                    merged_reqs.add(s)

    for src in sources:
        slug = src["slug"]
        source_slugs.append(slug)
        sd = gt / slug
        if not sd.exists():
            print(f"  WARN source missing: {slug}")
            continue
        if src["type"] == "dispatcher":
            sub_ops = sd / "ops"
            for op in sorted(sub_ops.iterdir()):
                if op.is_dir():
                    _add_action(op.name, op)
        else:  # leaf — copy the whole tool dir minus any nested ops
            _add_action(src["action"], sd)

    # dispatcher tool.py + manifest
    actions_doc = "\n".join(f"- {a}: {action_descs.get(a, '')}" for a in actions)
    (tmp_dir / "tool.py").write_text(
        DISPATCHER_TEMPLATE.format(family=family_label, actions_doc=actions_doc,
                                   actions_list=json.dumps(actions)),
        encoding="utf-8")
    props = {"action": {"type": "string", "enum": actions,
                        "description": "Which operation to run."}}
    for k, v in union_params.items():
        props[k] = v
    manifest = {
        "name": new_slug,
        "description": description,
        "version": "1.0.0",
        "parameters": {"type": "object", "properties": props, "required": ["action"]},
        "timeout": 60,
    }
    (tmp_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if merged_reqs:
        (tmp_dir / "requirements.txt").write_text("\n".join(sorted(merged_reqs)) + "\n", encoding="utf-8")

    # --- DB: union assignments, swap dirs, delete sources ---
    old_tools = Tool.query.filter(Tool.slug.in_(source_slugs)).all()
    agent_ids = set()
    for ot in old_tools:
        for at in AgentTool.query.filter_by(tool_id=ot.id).all():
            agent_ids.add(at.agent_id)

    # remove source dirs + the (possibly pre-existing) target dir, then move tmp in
    for slug in source_slugs:
        d = gt / slug
        if d.exists():
            shutil.rmtree(d)
    if new_dir.exists():
        shutil.rmtree(new_dir)
    tmp_dir.rename(new_dir)

    new_tool = Tool.query.filter_by(slug=new_slug).first()
    if new_tool is None:
        new_tool = Tool(slug=new_slug)
        db.session.add(new_tool)
    new_tool.name = new_slug
    new_tool.description = description
    new_tool.version = "1.0.0"
    new_tool.source = "workspace"
    new_tool.manifest_json = manifest
    new_tool.path = f"tools/{new_slug}"
    new_tool.timeout = 60
    db.session.flush()

    for aid in agent_ids:
        if not AgentTool.query.filter_by(tool_id=new_tool.id, agent_id=aid).first():
            db.session.add(AgentTool(tool_id=new_tool.id, agent_id=aid, enabled=True))
    for ot in old_tools:
        db.session.delete(ot)
    db.session.commit()
    return {"new": new_slug, "actions": len(actions), "agents": len(agent_ids)}


MERGES = [
    ("cloudflare", "Cloudflare", [
        {"type": "dispatcher", "slug": "cloudflare-read"},
        {"type": "leaf", "slug": "cloudflare-csp-updater", "action": "csp-update"},
    ], "Cloudflare operations: read zones/settings/firewall/tunnels/Zero-Trust and update CSP/security headers."),

    ("notion", "Notion", [
        {"type": "dispatcher", "slug": "notion-publish"},
        {"type": "dispatcher", "slug": "notion-audit"},
        {"type": "dispatcher", "slug": "notion-read"},
    ], "Notion operations: publish/create pages and reports, audit/restyle existing pages, and read/search/inspect pages and blocks."),
]


def main():
    import app as a
    ap = a.create_app()
    with ap.app_context():
        from app.models.tool import Tool
        before = Tool.query.count()
        for spec in MERGES:
            r = merge(*spec)
            print(f"  ✓ {r['new']:14} {r['actions']} actions, {r['agents']} agents")
        print(f"\n  tools: {before} -> {Tool.query.count()}")


if __name__ == "__main__":
    sys.exit(main())
