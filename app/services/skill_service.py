import json
import logging
import re
from datetime import datetime, timezone

from app.extensions import db
from app.models.agent import Agent
from app.models.skill import AgentSkill, Skill
from app.workspace.discovery import sync_global_skills_to_db
from app.workspace.manager import get_global_skills_path

logger = logging.getLogger(__name__)


def _slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def list_skills(agent_id=None):
    """Return skills. When agent_id is given, only skills assigned to that agent."""
    if agent_id:
        return (
            Skill.query
            .join(AgentSkill, AgentSkill.skill_id == Skill.id)
            .filter(AgentSkill.agent_id == agent_id)
            .order_by(Skill.name)
            .all()
        )
    return Skill.query.order_by(Skill.name).all()


def get_skill(skill_id):
    return db.session.get(Skill, skill_id)


def get_agent_skill(skill_id, agent_id):
    """Return the AgentSkill junction row for (skill, agent), or None."""
    return AgentSkill.query.filter_by(skill_id=skill_id, agent_id=agent_id).first()


def create_skill(agent_id, data):
    """Create a global skill: scaffold filesystem in _global/skills/ and create DB rows."""
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        raise ValueError("Agent not found")

    name = data["name"]
    slug = _slugify(name)

    if Skill.query.filter_by(slug=slug).first():
        raise ValueError(f"A skill with slug '{slug}' already exists in the global catalog")

    skill_dir = get_global_skills_path() / slug
    skill_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "name": name,
        "description": data.get("description", ""),
        "version": data.get("version", "0.1.0"),
    }
    (skill_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    skill_md = data.get("skill_md", f"# {name}\n\n{data.get('description', '')}\n")
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")

    skill = Skill(
        name=name,
        slug=slug,
        version=manifest["version"],
        description=manifest["description"],
        source="manual",
        manifest_json=manifest,
        path=f"skills/{slug}",
    )
    db.session.add(skill)
    db.session.flush()

    agent_skill = AgentSkill(
        agent_id=agent_id,
        skill_id=skill.id,
        enabled=True,
        created_at=datetime.now(timezone.utc),
    )
    db.session.add(agent_skill)
    db.session.commit()
    return skill


def toggle_skill(skill_id, agent_id):
    """Toggle enabled on the AgentSkill row for (skill, agent)."""
    ags = AgentSkill.query.filter_by(skill_id=skill_id, agent_id=agent_id).first()
    if ags is None:
        return None
    ags.enabled = not ags.enabled
    db.session.commit()
    return ags


def reload_skill(skill_id):
    """Re-read manifest from _global/skills/ and update the Skill row.

    If the _template/ copy has a strictly higher version (semver), its files
    are synced into _global/ first so the template always wins on Reload.
    """
    import shutil

    from app.workspace.manager import get_template_path

    skill = db.session.get(Skill, skill_id)
    if skill is None:
        return None

    global_dir = get_global_skills_path() / skill.slug
    template_dir = get_template_path() / "skills" / skill.slug

    # Sync template → global when template has a newer version
    if template_dir.is_dir():
        try:
            from app.workspace.manifest import load_manifest
            tmpl_manifest = load_manifest(template_dir / "manifest.json")
            global_manifest = load_manifest(global_dir / "manifest.json") if (global_dir / "manifest.json").exists() else {}
            if _version_gt(tmpl_manifest.get("version", "0"), global_manifest.get("version", "0")):
                global_dir.mkdir(parents=True, exist_ok=True)
                for src in template_dir.rglob("*"):
                    if src.is_file():
                        dst = global_dir / src.relative_to(template_dir)
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, dst)
                logger.info("reload_skill: synced %s from template (%s → %s)",
                            skill.slug,
                            global_manifest.get("version", "?"),
                            tmpl_manifest.get("version", "?"))
        except Exception:
            logger.warning("reload_skill: template sync failed for %s", skill.slug, exc_info=True)

    manifest_path = global_dir / "manifest.json"
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


def _version_gt(a: str, b: str) -> bool:
    """Return True if semver string a is strictly greater than b."""
    def _parse(v):
        try:
            return tuple(int(x) for x in str(v).split("."))
        except ValueError:
            return (0,)
    return _parse(a) > _parse(b)


def sync_agent_skills(agent_id):
    """Sync global skills to DB and ensure junction rows exist for the agent."""
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        return []
    return sync_global_skills_to_db(agent)


def assign_skill_to_agent(skill_id, agent_id):
    """Create an AgentSkill row giving the agent access to a global skill.

    Returns the AgentSkill row. Raises ValueError if already assigned.
    """
    skill = db.session.get(Skill, skill_id)
    if skill is None:
        raise ValueError("Skill not found")
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        raise ValueError("Agent not found")

    existing = AgentSkill.query.filter_by(skill_id=skill_id, agent_id=agent_id).first()
    if existing is not None:
        raise ValueError(f"Agent '{agent.name}' already has skill '{skill.slug}' assigned")

    ags = AgentSkill(
        agent_id=agent_id,
        skill_id=skill_id,
        enabled=True,
        created_at=datetime.now(timezone.utc),
    )
    db.session.add(ags)
    db.session.commit()
    return ags


def remove_skill_from_agent(skill_id, agent_id):
    """Remove an AgentSkill assignment. Does not delete the global Skill row."""
    ags = AgentSkill.query.filter_by(skill_id=skill_id, agent_id=agent_id).first()
    if ags is None:
        raise ValueError("Assignment not found")
    db.session.delete(ags)
    db.session.commit()
