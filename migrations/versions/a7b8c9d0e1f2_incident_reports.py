"""incident reports

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-06-24 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7b8c9d0e1f2'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'incident_reports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('agent_id', sa.Integer(), nullable=True),
        sa.Column('signature', sa.String(length=64), nullable=False),
        sa.Column('severity', sa.String(length=20), nullable=False),
        sa.Column('source', sa.String(length=255), nullable=True),
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('traceback', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('occurrences', sa.Integer(), nullable=False),
        sa.Column('diagnosis', sa.Text(), nullable=True),
        sa.Column('proposed_action', sa.String(length=10), nullable=True),
        sa.Column('proposed_title', sa.Text(), nullable=True),
        sa.Column('proposed_body', sa.Text(), nullable=True),
        sa.Column('proposed_patch_json', sa.JSON(), nullable=True),
        sa.Column('review_run_id', sa.Integer(), nullable=True),
        sa.Column('github_url', sa.Text(), nullable=True),
        sa.Column('resolution_note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ),
        sa.ForeignKeyConstraint(['review_run_id'], ['runs.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('incident_reports', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_incident_reports_agent_id'), ['agent_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_incident_reports_signature'), ['signature'], unique=False)
        batch_op.create_index(batch_op.f('ix_incident_reports_status'), ['status'], unique=False)


def downgrade():
    with op.batch_alter_table('incident_reports', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_incident_reports_status'))
        batch_op.drop_index(batch_op.f('ix_incident_reports_signature'))
        batch_op.drop_index(batch_op.f('ix_incident_reports_agent_id'))
    op.drop_table('incident_reports')
