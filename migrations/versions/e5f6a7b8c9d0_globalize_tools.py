"""globalize tools — move from per-agent to global catalog + agent_tools junction

Revision ID: e5f6a7b8c9d0
Revises: d3e4f5a6b7c8
Create Date: 2026-06-20

Changes:
  - Create agent_tools junction table (agent_id, tool_id, enabled)
  - Remove agent_id and enabled columns from tools
  - Replace uq_tool_agent_slug with uq_tool_slug
  - Data migration: deduplicate tools by slug, populate agent_tools
  - Filesystem migration: copy tool dirs to workspaces/_global/tools/

Mirrors b1a2c3d4e5f6_globalize_skills.
"""
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger(__name__)

revision = 'e5f6a7b8c9d0'
down_revision = 'd3e4f5a6b7c8'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Create agent_tools junction table
    op.create_table(
        'agent_tools',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('agent_id', sa.Integer(), sa.ForeignKey('agents.id'), nullable=False),
        sa.Column('tool_id', sa.Integer(), sa.ForeignKey('tools.id'), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('agent_id', 'tool_id', name='uq_agent_tool'),
    )
    op.create_index('ix_agent_tools_agent_id', 'agent_tools', ['agent_id'])
    op.create_index('ix_agent_tools_tool_id', 'agent_tools', ['tool_id'])

    # 2. Data migration via raw SQL (works for both SQLite and PostgreSQL)
    conn = op.get_bind()
    now_iso = datetime.now(timezone.utc).isoformat()

    rows = conn.execute(
        sa.text("SELECT id, agent_id, slug, enabled FROM tools ORDER BY id ASC")
    ).fetchall()

    # Determine canonical tool per slug (lowest id wins)
    canonical: dict[str, int] = {}
    for row in rows:
        slug = row[2]
        if slug not in canonical:
            canonical[slug] = row[0]

    # Populate agent_tools for every (agent_id, tool) pair, pointing to canonical
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
                "INSERT INTO agent_tools (agent_id, tool_id, enabled, created_at)"
                " VALUES (:a, :t, :e, :c)"
            ),
            {"a": agent_id, "t": canonical_id, "e": bool(enabled), "c": now_iso},
        )

    # Delete non-canonical duplicate tool rows (leave only one per slug)
    for row in rows:
        row_id, _, slug, _ = row[0], row[1], row[2], row[3]
        if row_id != canonical[slug]:
            conn.execute(sa.text("DELETE FROM tools WHERE id = :i"), {"i": row_id})

    # 3. Filesystem migration BEFORE dropping agent_id (we still need it to find dirs)
    _migrate_filesystem(conn)

    # 4. Normalize remaining tools' path to the _global layout and clear agent_id
    conn.execute(sa.text("UPDATE tools SET path = 'tools/' || slug"))

    # 5. Alter tools table: drop agent_id, enabled, swap unique constraint
    with op.batch_alter_table('tools', schema=None) as batch_op:
        try:
            batch_op.drop_index('ix_tools_agent_id')
        except Exception:
            pass
        try:
            batch_op.drop_constraint('uq_tool_agent_slug', type_='unique')
        except Exception:
            pass
        batch_op.drop_column('enabled')
        batch_op.drop_column('agent_id')
        batch_op.create_unique_constraint('uq_tool_slug', ['slug'])


def downgrade():
    with op.batch_alter_table('tools', schema=None) as batch_op:
        try:
            batch_op.drop_constraint('uq_tool_slug', type_='unique')
        except Exception:
            pass
        batch_op.add_column(sa.Column('agent_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('enabled', sa.Boolean(), nullable=True, server_default='1'))
        batch_op.create_unique_constraint('uq_tool_agent_slug', ['agent_id', 'slug'])
        batch_op.create_index('ix_tools_agent_id', ['agent_id'])

    # Restore agent_id from agent_tools (first assignment per tool)
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT tool_id, agent_id, enabled FROM agent_tools ORDER BY id ASC")
    ).fetchall()
    seen: set[int] = set()
    for tool_id, agent_id, enabled in rows:
        if tool_id in seen:
            continue
        seen.add(tool_id)
        conn.execute(
            sa.text("UPDATE tools SET agent_id = :a, enabled = :e WHERE id = :t"),
            {"a": agent_id, "e": bool(enabled), "t": tool_id},
        )

    op.drop_index('ix_agent_tools_tool_id', table_name='agent_tools')
    op.drop_index('ix_agent_tools_agent_id', table_name='agent_tools')
    op.drop_table('agent_tools')


def _migrate_filesystem(conn):
    """Copy existing tool directories from agent workspaces to _global/tools/."""
    try:
        from flask import current_app
        base = Path(current_app.config["WORKSPACES_BASE_PATH"]).resolve()
    except Exception:
        logger.warning("Cannot determine WORKSPACES_BASE_PATH — skipping filesystem migration")
        return

    global_tools = base / "_global" / "tools"
    global_tools.mkdir(parents=True, exist_ok=True)

    # Map canonical tool → owning agent workspace (the agent_id still on the row)
    try:
        rows = conn.execute(
            sa.text(
                "SELECT t.id, t.slug, t.path, a.slug AS agent_slug"
                " FROM tools t"
                " JOIN agents a ON a.id = t.agent_id"
                " ORDER BY t.id ASC"
            )
        ).fetchall()
    except Exception as exc:
        logger.warning("Filesystem migration query failed: %s", exc)
        return

    for tool_id, slug, rel_path, agent_slug in rows:
        dst = global_tools / slug
        if dst.exists():
            continue
        src = base / agent_slug / (rel_path or f"tools/{slug}")
        if not src.exists():
            src = base / agent_slug / "tools" / slug
        if src.exists():
            try:
                shutil.copytree(src, dst)
                logger.info("Migrated tool '%s' from %s to %s", slug, src, dst)
            except Exception as exc:
                logger.warning("Could not copy tool '%s': %s", slug, exc)
        else:
            logger.warning("Source dir not found for tool '%s' (agent %s)", slug, agent_slug)
