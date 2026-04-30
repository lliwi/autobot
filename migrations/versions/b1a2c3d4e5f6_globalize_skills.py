"""globalize skills — move from per-agent to global catalog + agent_skills junction

Revision ID: b1a2c3d4e5f6
Revises: e50cc2b75744
Create Date: 2026-04-30

Changes:
  - Create agent_skills junction table (agent_id, skill_id, enabled)
  - Remove agent_id and enabled columns from skills
  - Replace uq_skill_agent_slug with uq_skill_slug
  - Data migration: deduplicate skills, populate agent_skills
  - Filesystem migration: copy skill dirs to workspaces/_global/skills/
"""
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger(__name__)

revision = 'b1a2c3d4e5f6'
down_revision = 'e50cc2b75744'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Create agent_skills junction table
    op.create_table(
        'agent_skills',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('agent_id', sa.Integer(), sa.ForeignKey('agents.id'), nullable=False),
        sa.Column('skill_id', sa.Integer(), sa.ForeignKey('skills.id'), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('agent_id', 'skill_id', name='uq_agent_skill'),
    )
    op.create_index('ix_agent_skills_agent_id', 'agent_skills', ['agent_id'])
    op.create_index('ix_agent_skills_skill_id', 'agent_skills', ['skill_id'])

    # 2. Data migration via raw SQL (works for both SQLite and PostgreSQL)
    conn = op.get_bind()
    now_iso = datetime.now(timezone.utc).isoformat()

    # Find all current (agent_id, skill_id, enabled, slug) rows
    rows = conn.execute(
        sa.text("SELECT id, agent_id, slug, enabled FROM skills ORDER BY id ASC")
    ).fetchall()

    # Determine canonical skill per slug (lowest id wins)
    canonical: dict[str, int] = {}
    for row in rows:
        slug = row[2]
        if slug not in canonical:
            canonical[slug] = row[0]

    # Populate agent_skills for every (agent_id, skill) pair, pointing to canonical
    inserted_pairs: set[tuple[int, int]] = set()
    for row in rows:
        row_id, agent_id, slug, enabled = row[0], row[1], row[2], row[3]
        if agent_id is None:
            continue
        canonical_id = canonical[slug]
        pair = (agent_id, canonical_id)
        if pair in inserted_pairs:
            continue
        inserted_pairs.add(pair)
        conn.execute(
            sa.text(
                "INSERT INTO agent_skills (agent_id, skill_id, enabled, created_at)"
                " VALUES (:a, :s, :e, :c)"
            ),
            {"a": agent_id, "s": canonical_id, "e": bool(enabled), "c": now_iso},
        )

    # Delete non-canonical duplicate skill rows (leave only one per slug)
    for row in rows:
        row_id, _, slug, _ = row[0], row[1], row[2], row[3]
        if row_id != canonical[slug]:
            conn.execute(sa.text("DELETE FROM skills WHERE id = :i"), {"i": row_id})

    # 3. Alter skills table: drop agent_id, enabled, swap unique constraint
    with op.batch_alter_table('skills', schema=None) as batch_op:
        try:
            batch_op.drop_index('ix_skills_agent_id')
        except Exception:
            pass
        try:
            batch_op.drop_constraint('uq_skill_agent_slug', type_='unique')
        except Exception:
            pass
        batch_op.drop_column('enabled')
        batch_op.drop_column('agent_id')
        batch_op.create_unique_constraint('uq_skill_slug', ['slug'])

    # 4. Best-effort filesystem migration: copy to _global/skills/
    _migrate_filesystem(conn)


def downgrade():
    # Restore enabled and agent_id columns to skills (one representative agent per skill)
    with op.batch_alter_table('skills', schema=None) as batch_op:
        try:
            batch_op.drop_constraint('uq_skill_slug', type_='unique')
        except Exception:
            pass
        batch_op.add_column(sa.Column('agent_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('enabled', sa.Boolean(), nullable=True, server_default='1'))
        batch_op.create_unique_constraint('uq_skill_agent_slug', ['agent_id', 'slug'])
        batch_op.create_index('ix_skills_agent_id', ['agent_id'])

    # Restore agent_id from agent_skills (first assignment per skill)
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT skill_id, agent_id, enabled FROM agent_skills"
            " ORDER BY id ASC"
        )
    ).fetchall()
    seen: set[int] = set()
    for skill_id, agent_id, enabled in rows:
        if skill_id in seen:
            continue
        seen.add(skill_id)
        conn.execute(
            sa.text("UPDATE skills SET agent_id = :a, enabled = :e WHERE id = :s"),
            {"a": agent_id, "e": bool(enabled), "s": skill_id},
        )

    op.drop_index('ix_agent_skills_skill_id', table_name='agent_skills')
    op.drop_index('ix_agent_skills_agent_id', table_name='agent_skills')
    op.drop_table('agent_skills')


def _migrate_filesystem(conn):
    """Copy existing skill directories from agent workspaces to _global/skills/."""
    try:
        from flask import current_app
        base = Path(current_app.config["WORKSPACES_BASE_PATH"]).resolve()
    except Exception:
        logger.warning("Cannot determine WORKSPACES_BASE_PATH — skipping filesystem migration")
        return

    global_skills = base / "_global" / "skills"
    global_skills.mkdir(parents=True, exist_ok=True)

    # Map skill_id → agent_slug (canonical assignment) and skill path
    try:
        rows = conn.execute(
            sa.text(
                "SELECT s.id, s.slug, s.path, a.slug AS agent_slug"
                " FROM skills s"
                " JOIN agent_skills ags ON ags.skill_id = s.id"
                " JOIN agents a ON a.id = ags.agent_id"
                " ORDER BY ags.id ASC"
            )
        ).fetchall()
    except Exception as exc:
        logger.warning("Filesystem migration query failed: %s", exc)
        return

    seen_slugs: set[str] = set()
    for skill_id, slug, rel_path, agent_slug in rows:
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        dst = global_skills / slug
        if dst.exists():
            continue

        # Try the stored path, fall back to skills/{slug}
        src = base / agent_slug / (rel_path or f"skills/{slug}")
        if not src.exists():
            src = base / agent_slug / "skills" / slug
        if src.exists():
            try:
                shutil.copytree(src, dst)
                logger.info("Migrated skill '%s' from %s to %s", slug, src, dst)
            except Exception as exc:
                logger.warning("Could not copy skill '%s': %s", slug, exc)
        else:
            logger.warning("Source dir not found for skill '%s' (agent %s)", slug, agent_slug)
