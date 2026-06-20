"""Export and import the full Autobot state as a portable tar.gz bundle.

The bundle is self-contained enough to clone an Autobot install onto another
machine. It carries DB rows (agents, tools, skills, credentials, packages) plus
the on-disk workspaces, and — optionally — the raw ``.env``.

Design notes:
 - Credentials are exported **decrypted** in ``credentials.json`` when the
   caller opts in with ``include_secrets=True``. Re-encryption happens on
   import with the destination's ``TOKEN_ENCRYPTION_KEY``, so the two installs
   don't need to share the same key.
 - Agent identity on disk is keyed by slug, not by DB id. Parent-child links
   are serialized as ``parent_slug`` and re-resolved after the first pass on
   import.
 - ``.venv`` and ``runs/`` are excluded from workspace dirs — rebuilt or left
   empty on the target. ``__pycache__`` is also stripped.
 - Re-imports either skip existing rows or overwrite them (``overwrite=True``).
   Overwrite replaces the row in place (same id) so FK references survive.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app

from app.extensions import db
from app.models.agent import Agent
from app.models.credential import Credential
from app.models.package_installation import PackageInstallation
from app.models.skill import Skill
from app.models.tool import Tool
from app.services import credential_service


logger = logging.getLogger(__name__)


SCHEMA_VERSION = 1

# Files/dirs we strip out of each workspace tree — rebuilt or re-derived on
# the target install, so no reason to bloat the bundle with them.
# .bin/ contains symlinks to host system binaries (e.g. ffmpeg) that are
# machine-specific and must be re-installed on the target.
_WORKSPACE_EXCLUDE_NAMES = {".venv", "runs", "__pycache__", ".pytest_cache", ".mypy_cache", ".bin"}


@dataclass
class ExportReport:
    """Summary of what ``export_bundle`` wrote. Safe to print verbatim."""
    path: str
    agents: int = 0
    tools: int = 0
    skills: int = 0
    credentials: int = 0
    packages: int = 0
    scheduled_tasks: int = 0
    objectives: int = 0
    workspaces: int = 0
    included_env: bool = False
    included_secrets: bool = False


@dataclass
class ImportReport:
    """Summary of what ``import_bundle`` did. ``skipped`` counts conflicts."""
    agents_created: int = 0
    agents_updated: int = 0
    agents_skipped: int = 0
    tools_created: int = 0
    tools_updated: int = 0
    skills_created: int = 0
    skills_updated: int = 0
    credentials_created: int = 0
    credentials_updated: int = 0
    packages_created: int = 0
    packages_updated: int = 0
    tasks_created: int = 0
    tasks_updated: int = 0
    objectives_created: int = 0
    objectives_updated: int = 0
    workspaces_restored: int = 0
    env_written: bool = False
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_bundle(
    output_path: str,
    *,
    include_env: bool = False,
    include_secrets: bool = False,
) -> ExportReport:
    """Write a tar.gz snapshot of the entire Autobot install.

    ``include_secrets`` controls whether credential values are serialised in
    plaintext — off by default because the tarball is not encrypted.
    ``include_env`` copies the project's ``.env`` verbatim; same caveat.
    """
    report = ExportReport(
        path=output_path,
        included_env=include_env,
        included_secrets=include_secrets,
    )

    agents = Agent.query.order_by(Agent.id.asc()).all()
    agents_payload = [_serialize_agent(a) for a in agents]
    report.agents = len(agents_payload)

    from app.models.tool import AgentTool
    agent_tools = (
        AgentTool.query
        .join(Tool, AgentTool.tool_id == Tool.id)
        .order_by(Tool.id.asc(), AgentTool.agent_id.asc())
        .all()
    )
    tools_payload = [_serialize_tool_assignment(at) for at in agent_tools if at.agent]
    report.tools = len(tools_payload)

    from app.models.skill import AgentSkill
    agent_skills = (
        AgentSkill.query
        .join(Skill, AgentSkill.skill_id == Skill.id)
        .order_by(Skill.id.asc(), AgentSkill.agent_id.asc())
        .all()
    )
    skills_payload = [_serialize_skill_assignment(ags) for ags in agent_skills if ags.agent]
    report.skills = len(skills_payload)

    packages = PackageInstallation.query.order_by(PackageInstallation.id.asc()).all()
    packages_payload = [_serialize_package(p) for p in packages if p.agent]
    report.packages = len(packages_payload)

    credentials_payload = []
    if include_secrets:
        for row in Credential.query.order_by(Credential.id.asc()).all():
            credentials_payload.append(_serialize_credential(row))
    report.credentials = len(credentials_payload)

    from app.models.scheduled_task import ScheduledTask
    tasks = ScheduledTask.query.order_by(ScheduledTask.id.asc()).all()
    tasks_payload = [_serialize_task(t) for t in tasks if t.agent]
    report.scheduled_tasks = len(tasks_payload)

    from app.models.objective import Objective
    open_objs = (
        Objective.query
        .filter(Objective.status.in_(("active", "blocked", "waiting")))
        .order_by(Objective.id.asc())
        .all()
    )
    objectives_payload = [_serialize_objective(o) for o in open_objs if o.agent]
    report.objectives = len(objectives_payload)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "agents": report.agents,
            "tools": report.tools,
            "skills": report.skills,
            "credentials": report.credentials,
            "packages": report.packages,
            "scheduled_tasks": report.scheduled_tasks,
            "objectives": report.objectives,
        },
        "options": {
            "include_env": include_env,
            "include_secrets": include_secrets,
        },
    }

    output_path = str(Path(output_path).expanduser().resolve())
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    with tarfile.open(output_path, "w:gz") as tar:
        _add_json(tar, "manifest.json", manifest)
        _add_json(tar, "agents.json", agents_payload)
        _add_json(tar, "tools.json", tools_payload)
        _add_json(tar, "skills.json", skills_payload)
        _add_json(tar, "packages.json", packages_payload)
        _add_json(tar, "credentials.json", credentials_payload)
        _add_json(tar, "tasks.json", tasks_payload)
        _add_json(tar, "objectives.json", objectives_payload)

        for agent in agents:
            ws = _resolve_workspace_path(agent)
            if not ws.exists():
                logger.warning("Workspace not found for agent %s at %s", agent.slug, ws)
                continue
            _add_workspace_dir(tar, agent.slug, ws)
            report.workspaces += 1

        # Archive the shared _global/ tree so global skills/tools files travel too.
        global_dir = Path(current_app.config["WORKSPACES_BASE_PATH"]).resolve() / "_global"
        if global_dir.exists():
            _add_workspace_dir(tar, "_global", global_dir)

        if include_env:
            env_path = _project_root() / ".env"
            if env_path.exists():
                tar.add(str(env_path), arcname=".env")
            else:
                logger.warning("--include-env requested but .env does not exist at %s", env_path)
                report.included_env = False

    return report


def _serialize_agent(a: Agent) -> dict:
    parent_slug = a.parent_agent.slug if a.parent_agent else None
    return {
        "slug": a.slug,
        "name": a.name,
        "status": a.status,
        "model_name": a.model_name,
        "parent_slug": parent_slug,
        "heartbeat_interval": a.heartbeat_interval,
        "group_response_policy": a.group_response_policy,
        "review_effort": a.review_effort,
        "review_token_budget_daily": a.review_token_budget_daily,
        # workspace_path is regenerated on import from destination config
    }


def _serialize_tool_assignment(at) -> dict:
    """Serialize a global tool + its per-agent assignment for the bundle."""
    t = at.tool
    return {
        "agent_slug": at.agent.slug,
        "slug": t.slug,
        "name": t.name,
        "version": t.version,
        "description": t.description,
        "source": t.source,
        "enabled": at.enabled,
        "manifest_json": t.manifest_json,
        "path": t.path,
        "timeout": t.timeout,
    }


def _serialize_skill_assignment(ags) -> dict:
    """Serialize a skill + its per-agent assignment for the bundle."""
    s = ags.skill
    return {
        "agent_slug": ags.agent.slug,
        "slug": s.slug,
        "name": s.name,
        "version": s.version,
        "description": s.description,
        "source": s.source,
        "enabled": ags.enabled,
        "manifest_json": s.manifest_json,
        "path": s.path,
    }


def _serialize_task(t) -> dict:
    """Serialize a scheduled task. Runtime state (last/next run, retries) is
    intentionally dropped — the scheduler recomputes it on the destination.
    """
    return {
        "agent_slug": t.agent.slug,
        "name": t.name,
        "task_type": t.task_type,
        "schedule_expr": t.schedule_expr,
        "schedule_config": t.schedule_config,
        "timezone": t.timezone,
        "payload_json": t.payload_json,
        "enabled": t.enabled,
        "max_retries": t.max_retries,
    }


def _serialize_objective(o) -> dict:
    """Serialize an open objective (active/blocked/waiting) so in-flight
    autonomous work survives a restore. Done/cancelled ones are not exported.
    """
    return {
        "agent_slug": o.agent.slug,
        "title": o.title,
        "description": o.description,
        "status": o.status,
        "context_json": o.context_json,
    }


def _serialize_package(p: PackageInstallation) -> dict:
    return {
        "agent_slug": p.agent.slug,
        "name": p.name,
        "spec": p.spec,
        "installed_version": p.installed_version,
        # Force re-install on destination regardless of source status, unless
        # it was rejected — we keep that signal so admins don't reopen it.
        "status": "rejected" if p.status == "rejected" else "pending_review",
        "reason": p.reason,
    }


def _serialize_credential(row: Credential) -> dict:
    """Decrypt the credential value for export. Only called when the caller
    passed ``include_secrets=True``; we assume they've accepted the risk.
    """
    pair = credential_service.get_credential_pair(row.name, agent_id=row.agent_id)
    data = {
        "agent_slug": row.agent.slug if row.agent else None,
        "name": row.name,
        "description": row.description,
        "credential_type": row.credential_type,
    }
    if row.credential_type == "user_password":
        data["username"] = row.username
        data["value"] = pair["password"] if pair else None
    else:
        data["value"] = pair["value"] if pair else None
    return data


def _add_json(tar: tarfile.TarFile, arcname: str, payload) -> None:
    raw = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    info = tarfile.TarInfo(name=arcname)
    info.size = len(raw)
    info.mtime = int(datetime.now(timezone.utc).timestamp())
    info.mode = 0o644
    tar.addfile(info, io.BytesIO(raw))


def _add_workspace_dir(tar: tarfile.TarFile, slug: str, src: Path) -> None:
    arcroot = f"workspaces/{slug}"

    def _filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
        # Arcname looks like "workspaces/<slug>/foo/bar" — strip the prefix
        # before checking exclude names so we catch nested matches too.
        rel = tarinfo.name[len(arcroot):].lstrip("/")
        parts = rel.split("/") if rel else []
        if any(part in _WORKSPACE_EXCLUDE_NAMES for part in parts):
            return None
        return tarinfo

    tar.add(str(src), arcname=arcroot, filter=_filter)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def import_bundle(input_path: str, *, overwrite: bool = False) -> ImportReport:
    """Load a bundle written by ``export_bundle`` into the current install.

    When ``overwrite`` is True, existing rows with matching slugs/names are
    updated in place (keeping their id so FKs survive). Otherwise conflicts
    are skipped and logged in ``report.warnings``.
    """
    input_path = str(Path(input_path).expanduser().resolve())
    report = ImportReport()

    with tempfile.TemporaryDirectory(prefix="autobot-import-") as tmpdir:
        _safe_extract(input_path, tmpdir)
        tmp = Path(tmpdir)

        manifest = _load_json(tmp / "manifest.json")
        if not manifest or manifest.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported bundle schema: got {manifest.get('schema_version')},"
                f" expected {SCHEMA_VERSION}"
            )

        agents_payload = _load_json(tmp / "agents.json") or []
        tools_payload = _load_json(tmp / "tools.json") or []
        skills_payload = _load_json(tmp / "skills.json") or []
        packages_payload = _load_json(tmp / "packages.json") or []
        credentials_payload = _load_json(tmp / "credentials.json") or []
        tasks_payload = _load_json(tmp / "tasks.json") or []
        objectives_payload = _load_json(tmp / "objectives.json") or []

        slug_to_agent = _import_agents(agents_payload, tmp, overwrite, report)

        # Fix up parent links now that all agents exist.
        _apply_parent_links(agents_payload, slug_to_agent)

        _import_tools(tools_payload, slug_to_agent, overwrite, report)
        _import_skills(skills_payload, slug_to_agent, overwrite, report)
        _import_packages(packages_payload, slug_to_agent, overwrite, report)
        _import_credentials(credentials_payload, slug_to_agent, overwrite, report)
        _import_tasks(tasks_payload, slug_to_agent, overwrite, report)
        _import_objectives(objectives_payload, slug_to_agent, overwrite, report)

        # .env last so the rest of the import can't accidentally read
        # half-applied state.
        env_src = tmp / ".env"
        if env_src.exists():
            _maybe_write_env(env_src, overwrite, report)

    db.session.commit()
    return report


def _safe_extract(archive: str, dest: str) -> None:
    """Extract a tar.gz into ``dest`` without allowing path escape.

    The archive can come from anywhere so we reject any member whose resolved
    path lies outside the target dir (the classic ``../etc/passwd`` trick).
    """
    dest_path = Path(dest).resolve()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            target = (dest_path / member.name).resolve()
            if not str(target).startswith(str(dest_path) + os.sep) and target != dest_path:
                raise ValueError(f"Refusing to extract outside target: {member.name}")
        tar.extractall(dest)


def _load_json(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _import_agents(
    payload: list[dict],
    bundle_root: Path,
    overwrite: bool,
    report: ImportReport,
) -> dict[str, Agent]:
    """Create or update Agent rows and restore their workspace directories."""
    slug_to_agent: dict[str, Agent] = {}
    workspaces_base = Path(current_app.config["WORKSPACES_BASE_PATH"]).resolve()
    workspaces_base.mkdir(parents=True, exist_ok=True)

    # Pass 1: upsert rows, deferring parent linkage (FK may not exist yet).
    for entry in payload:
        slug = entry.get("slug")
        if not slug:
            report.warnings.append("agents.json: entry without slug, skipped")
            continue

        existing = Agent.query.filter_by(slug=slug).first()
        workspace_path = str(workspaces_base / slug)

        if existing and not overwrite:
            slug_to_agent[slug] = existing
            report.agents_skipped += 1
            continue

        if existing:
            existing.name = entry.get("name") or existing.name
            existing.status = entry.get("status") or existing.status
            existing.model_name = entry.get("model_name") or existing.model_name
            existing.workspace_path = workspace_path
            existing.heartbeat_interval = entry.get("heartbeat_interval")
            existing.group_response_policy = (
                entry.get("group_response_policy") or existing.group_response_policy
            )
            existing.review_effort = entry.get("review_effort", existing.review_effort)
            existing.review_token_budget_daily = entry.get("review_token_budget_daily")
            slug_to_agent[slug] = existing
            report.agents_updated += 1
        else:
            agent = Agent(
                slug=slug,
                name=entry.get("name") or slug,
                status=entry.get("status") or "inactive",
                model_name=entry.get("model_name") or "gpt-5.2",
                workspace_path=workspace_path,
                heartbeat_interval=entry.get("heartbeat_interval"),
                group_response_policy=entry.get("group_response_policy") or "mention",
                review_effort=entry.get("review_effort", 3),
                review_token_budget_daily=entry.get("review_token_budget_daily"),
            )
            db.session.add(agent)
            db.session.flush()
            slug_to_agent[slug] = agent
            report.agents_created += 1

        # Restore workspace dir if the bundle carries one for this slug.
        src_ws = bundle_root / "workspaces" / slug
        if src_ws.exists():
            _restore_workspace(src_ws, Path(workspace_path), overwrite)
            report.workspaces_restored += 1

    # Restore the shared _global/ tree (global skills + tools files).
    src_global = bundle_root / "workspaces" / "_global"
    if src_global.exists():
        _restore_workspace(src_global, workspaces_base / "_global", overwrite)

    return slug_to_agent


def _restore_workspace(src: Path, dest: Path, overwrite: bool) -> None:
    """Copy an extracted workspace into its destination slot.

    We always preserve files the bundle doesn't carry (the dest might have a
    freshly-built ``.venv`` for example). Existing files are replaced only when
    ``overwrite`` is True — otherwise we keep the destination version.

    Symlinks are recreated as symlinks. Dangling symlinks (target missing inside
    the bundle — e.g. .bin/ffmpeg pointing to a host binary) are skipped with a
    warning instead of raising FileNotFoundError.
    """
    dest.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(src):
        rel_root = Path(root).relative_to(src)
        dest_root = dest / rel_root
        dest_root.mkdir(parents=True, exist_ok=True)
        for name in files:
            src_file = Path(root) / name
            dest_file = dest_root / name
            if dest_file.exists() and not overwrite:
                continue
            if src_file.is_symlink():
                target = os.readlink(src_file)
                # Skip dangling symlinks — their target is host-specific and
                # doesn't exist inside the extracted bundle (e.g. .bin/ffmpeg).
                resolved = (src_file.parent / target).resolve()
                if not resolved.exists():
                    logger.warning(
                        "restore: skipping dangling symlink %s -> %s",
                        src_file.relative_to(src), target,
                    )
                    continue
                if dest_file.is_symlink() or dest_file.exists():
                    dest_file.unlink()
                os.symlink(target, dest_file)
            else:
                shutil.copy2(src_file, dest_file)


def _apply_parent_links(payload: list[dict], slug_to_agent: dict[str, Agent]) -> None:
    for entry in payload:
        slug = entry.get("slug")
        parent_slug = entry.get("parent_slug")
        if not slug or not parent_slug:
            continue
        child = slug_to_agent.get(slug)
        parent = slug_to_agent.get(parent_slug)
        if child and parent:
            child.parent_agent_id = parent.id
    db.session.flush()


def _import_tools(
    payload: list[dict],
    slug_to_agent: dict[str, Agent],
    overwrite: bool,
    report: ImportReport,
) -> None:
    from datetime import datetime, timezone
    from app.models.tool import AgentTool

    for entry in payload:
        slug = entry.get("slug")
        agent_slug = entry.get("agent_slug")
        agent = slug_to_agent.get(agent_slug) if agent_slug else None

        # Upsert the global Tool row (one per slug, no agent ownership)
        tool = Tool.query.filter_by(slug=slug).first()
        if tool is None:
            tool = Tool(
                name=entry.get("name") or slug,
                slug=slug,
                version=entry.get("version") or "0.1.0",
                description=entry.get("description"),
                source=entry.get("source") or "workspace",
                manifest_json=entry.get("manifest_json"),
                path=entry.get("path") or f"tools/{slug}",
                timeout=entry.get("timeout"),
            )
            db.session.add(tool)
            db.session.flush()
            report.tools_created += 1
        elif overwrite:
            tool.name = entry.get("name") or tool.name
            tool.version = entry.get("version") or tool.version
            tool.description = entry.get("description")
            tool.source = entry.get("source") or tool.source
            tool.manifest_json = entry.get("manifest_json")
            tool.path = entry.get("path") or tool.path
            tool.timeout = entry.get("timeout")
            report.tools_updated += 1

        # Create AgentTool assignment if agent is known
        if agent is None:
            if agent_slug:
                report.warnings.append(
                    f"tools.json: agent '{agent_slug}' missing — tool '{slug}' imported globally without assignment"
                )
            continue

        at = AgentTool.query.filter_by(agent_id=agent.id, tool_id=tool.id).first()
        if at is None:
            db.session.add(AgentTool(
                agent_id=agent.id,
                tool_id=tool.id,
                enabled=bool(entry.get("enabled", True)),
                created_at=datetime.now(timezone.utc),
            ))
        elif overwrite:
            at.enabled = bool(entry.get("enabled", True))


def _import_tasks(
    payload: list[dict],
    slug_to_agent: dict[str, Agent],
    overwrite: bool,
    report: ImportReport,
) -> None:
    from app.models.scheduled_task import ScheduledTask

    for entry in payload:
        agent = slug_to_agent.get(entry.get("agent_slug"))
        if not agent:
            report.warnings.append(
                f"tasks.json: skipped task {entry.get('name')!r} — agent "
                f"'{entry.get('agent_slug')}' missing"
            )
            continue

        # Identify an existing task by its defining fields so re-import is idempotent.
        existing = ScheduledTask.query.filter_by(
            agent_id=agent.id,
            name=entry.get("name"),
            task_type=entry.get("task_type"),
            schedule_expr=entry.get("schedule_expr"),
        ).first()
        if existing and not overwrite:
            continue

        if existing:
            existing.schedule_config = entry.get("schedule_config")
            existing.timezone = entry.get("timezone") or "UTC"
            existing.payload_json = entry.get("payload_json")
            existing.enabled = bool(entry.get("enabled", True))
            existing.max_retries = entry.get("max_retries", 3) or 3
            report.tasks_updated += 1
        else:
            db.session.add(ScheduledTask(
                agent_id=agent.id,
                name=entry.get("name"),
                task_type=entry.get("task_type") or "cron",
                schedule_expr=entry.get("schedule_expr"),
                schedule_config=entry.get("schedule_config"),
                timezone=entry.get("timezone") or "UTC",
                payload_json=entry.get("payload_json"),
                enabled=bool(entry.get("enabled", True)),
                max_retries=entry.get("max_retries", 3) or 3,
            ))
            report.tasks_created += 1


def _import_objectives(
    payload: list[dict],
    slug_to_agent: dict[str, Agent],
    overwrite: bool,
    report: ImportReport,
) -> None:
    from app.models.objective import Objective

    for entry in payload:
        agent = slug_to_agent.get(entry.get("agent_slug"))
        if not agent:
            report.warnings.append(
                f"objectives.json: skipped {entry.get('title')!r} — agent "
                f"'{entry.get('agent_slug')}' missing"
            )
            continue

        existing = (
            Objective.query
            .filter_by(agent_id=agent.id, title=entry.get("title"))
            .filter(Objective.status.in_(("active", "blocked", "waiting")))
            .first()
        )
        if existing and not overwrite:
            continue

        if existing:
            existing.description = entry.get("description")
            existing.status = entry.get("status") or "active"
            existing.context_json = entry.get("context_json")
            report.objectives_updated += 1
        else:
            db.session.add(Objective(
                agent_id=agent.id,
                title=entry.get("title"),
                description=entry.get("description"),
                status=entry.get("status") or "active",
                context_json=entry.get("context_json"),
            ))
            report.objectives_created += 1


def _import_skills(
    payload: list[dict],
    slug_to_agent: dict[str, Agent],
    overwrite: bool,
    report: ImportReport,
) -> None:
    from datetime import datetime, timezone
    from app.models.skill import AgentSkill

    for entry in payload:
        slug = entry.get("slug")
        agent_slug = entry.get("agent_slug")
        agent = slug_to_agent.get(agent_slug) if agent_slug else None

        # Upsert the global Skill row (one per slug, no agent ownership)
        skill = Skill.query.filter_by(slug=slug).first()
        if skill is None:
            skill = Skill(
                name=entry.get("name") or slug,
                slug=slug,
                version=entry.get("version") or "0.1.0",
                description=entry.get("description"),
                source=entry.get("source") or "manual",
                manifest_json=entry.get("manifest_json"),
                path=entry.get("path") or f"skills/{slug}",
            )
            db.session.add(skill)
            db.session.flush()
            report.skills_created += 1
        elif overwrite:
            skill.name = entry.get("name") or skill.name
            skill.version = entry.get("version") or skill.version
            skill.description = entry.get("description")
            skill.source = entry.get("source") or skill.source
            skill.manifest_json = entry.get("manifest_json")
            skill.path = entry.get("path") or skill.path
            report.skills_updated += 1

        # Create AgentSkill assignment if agent is known
        if agent is None:
            if agent_slug:
                report.warnings.append(
                    f"skills.json: agent '{agent_slug}' missing — skill '{slug}' imported globally without assignment"
                )
            continue

        ags = AgentSkill.query.filter_by(agent_id=agent.id, skill_id=skill.id).first()
        if ags is None:
            db.session.add(AgentSkill(
                agent_id=agent.id,
                skill_id=skill.id,
                enabled=bool(entry.get("enabled", True)),
                created_at=datetime.now(timezone.utc),
            ))
        elif overwrite:
            ags.enabled = bool(entry.get("enabled", ags.enabled))


def _import_packages(
    payload: list[dict],
    slug_to_agent: dict[str, Agent],
    overwrite: bool,
    report: ImportReport,
) -> None:
    for entry in payload:
        agent = slug_to_agent.get(entry.get("agent_slug"))
        if not agent:
            report.warnings.append(
                f"packages.json: skipped '{entry.get('name')}' — agent '{entry.get('agent_slug')}' missing"
            )
            continue

        existing = PackageInstallation.query.filter_by(
            agent_id=agent.id, name=entry.get("name")
        ).first()
        if existing and not overwrite:
            continue

        if existing:
            existing.spec = entry.get("spec") or existing.spec
            existing.status = entry.get("status") or "pending_review"
            existing.reason = entry.get("reason")
            report.packages_updated += 1
        else:
            db.session.add(PackageInstallation(
                agent_id=agent.id,
                name=entry.get("name"),
                spec=entry.get("spec") or entry.get("name"),
                installed_version=None,  # re-resolved by installer
                status=entry.get("status") or "pending_review",
                reason=entry.get("reason"),
            ))
            report.packages_created += 1


def _import_credentials(
    payload: list[dict],
    slug_to_agent: dict[str, Agent],
    overwrite: bool,
    report: ImportReport,
) -> None:
    """Re-encrypt each credential with *this* install's key."""
    for entry in payload:
        name = entry.get("name")
        if not name:
            continue
        value = entry.get("value")
        if value is None:
            report.warnings.append(f"credentials.json: '{name}' has no value, skipped")
            continue

        agent_slug = entry.get("agent_slug")
        agent_id = None
        if agent_slug:
            agent = slug_to_agent.get(agent_slug)
            if not agent:
                report.warnings.append(
                    f"credentials.json: skipped '{name}' — agent '{agent_slug}' missing"
                )
                continue
            agent_id = agent.id

        existing = Credential.query.filter_by(agent_id=agent_id, name=name).first()
        if existing and not overwrite:
            continue

        try:
            credential_service.set_credential(
                name=name,
                value=value,
                description=entry.get("description"),
                agent_id=agent_id,
                credential_type=entry.get("credential_type") or "token",
                username=entry.get("username"),
            )
        except credential_service.CredentialError as e:
            report.warnings.append(f"credentials.json: '{name}' rejected ({e})")
            continue

        if existing:
            report.credentials_updated += 1
        else:
            report.credentials_created += 1


def _maybe_write_env(src: Path, overwrite: bool, report: ImportReport) -> None:
    dest = _project_root() / ".env"
    if dest.exists() and not overwrite:
        report.warnings.append(
            ".env already exists on target — pass --overwrite to replace it"
        )
        return
    shutil.copy2(src, dest)
    report.env_written = True


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _resolve_workspace_path(agent: Agent) -> Path:
    """Return the real filesystem path for this agent's workspace.

    Some older rows stored an absolute path pointing to ``/workspaces/<slug>``
    (the container mount). If that path doesn't exist, fall back to the
    current install's ``WORKSPACES_BASE_PATH`` + slug so exports from one
    environment still work in another.
    """
    raw = Path(agent.workspace_path)
    if raw.exists():
        return raw
    base = Path(current_app.config["WORKSPACES_BASE_PATH"]).resolve()
    return base / agent.slug


# Validate bundle paths don't look malicious. Exposed so the CLI can print
# nice error messages before opening the archive.
_VALID_BUNDLE_NAME = re.compile(r"^[A-Za-z0-9_./\- ]+\.(tar\.gz|tgz)$")


def is_valid_bundle_name(path: str) -> bool:
    return bool(_VALID_BUNDLE_NAME.match(os.path.basename(path)))
