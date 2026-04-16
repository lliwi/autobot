import json
import re
import shutil
from pathlib import Path

from app.extensions import db
from app.models.agent import Agent
from app.models.skill import Skill
from app.workspace.discovery import sync_skills_to_db
from app.workspace.manager import get_workspace_path


def _slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def list_skills(agent_id=None):
    query = Skill.query
    if agent_id:
        query = query.filter_by(agent_id=agent_id)
    return query.order_by(Skill.name).all()


def get_skill(skill_id):
    return db.session.get(Skill, skill_id)


def create_skill(agent_id, data):
    """Create a skill: scaffold filesystem structure and DB row."""
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        raise ValueError("Agent not found")

    name = data["name"]
    slug = _slugify(name)
    workspace = get_workspace_path(agent)
    skill_dir = workspace / "skills" / slug
    skill_dir.mkdir(parents=True, exist_ok=True)

    # Write manifest
    manifest = {
        "name": name,
        "description": data.get("description", ""),
        "version": data.get("version", "0.1.0"),
    }
    (skill_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Write SKILL.md
    skill_md = data.get("skill_md", f"# {name}\n\n{data.get('description', '')}\n")
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")

    skill = Skill(
        agent_id=agent_id,
        name=name,
        slug=slug,
        version=manifest["version"],
        description=manifest["description"],
        source="manual",
        enabled=True,
        manifest_json=manifest,
        path=f"skills/{slug}",
    )
    db.session.add(skill)
    db.session.commit()
    return skill


def toggle_skill(skill_id):
    skill = db.session.get(Skill, skill_id)
    if skill is None:
        return None
    skill.enabled = not skill.enabled
    db.session.commit()
    return skill


def reload_skill(skill_id):
    """Re-read manifest from filesystem and update DB row."""
    skill = db.session.get(Skill, skill_id)
    if skill is None:
        return None

    agent = db.session.get(Agent, skill.agent_id)
    workspace = get_workspace_path(agent)
    manifest_path = workspace / skill.path / "manifest.json"

    if not manifest_path.exists():
        return skill

    from app.workspace.manifest import load_manifest, validate_skill_manifest

    try:
        manifest = load_manifest(manifest_path)
        errors = validate_skill_manifest(manifest)
        if errors:
            return skill
    except ValueError:
        return skill

    skill.name = manifest.get("name", skill.name)
    skill.description = manifest.get("description", skill.description)
    skill.version = manifest.get("version", skill.version)
    skill.manifest_json = manifest
    db.session.commit()
    return skill


def sync_agent_skills(agent_id):
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        return []
    return sync_skills_to_db(agent)


def share_skill(skill_id, target_agent_id):
    """Copy a skill's filesystem directory from its source agent to a target agent
    and create the corresponding Skill row. Returns the new Skill or raises ValueError.
    """
    source = db.session.get(Skill, skill_id)
    if source is None:
        raise ValueError("Skill not found")

    source_agent = db.session.get(Agent, source.agent_id)
    target_agent = db.session.get(Agent, target_agent_id)
    if target_agent is None:
        raise ValueError("Target agent not found")
    if source_agent is None:
        raise ValueError("Source agent not found")
    if source_agent.id == target_agent.id:
        raise ValueError("Source and target agents are the same")

    existing = Skill.query.filter_by(agent_id=target_agent.id, slug=source.slug).first()
    if existing is not None:
        raise ValueError(f"Agent '{target_agent.name}' already has a skill with slug '{source.slug}'")

    source_dir = get_workspace_path(source_agent) / source.path
    target_dir = get_workspace_path(target_agent) / source.path
    if not source_dir.exists():
        raise ValueError(f"Source skill directory missing: {source_dir}")

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, target_dir)

    copy = Skill(
        agent_id=target_agent.id,
        name=source.name,
        slug=source.slug,
        version=source.version,
        description=source.description,
        source=source.source,
        enabled=True,
        manifest_json=source.manifest_json,
        path=source.path,
    )
    db.session.add(copy)
    db.session.commit()
    return copy
