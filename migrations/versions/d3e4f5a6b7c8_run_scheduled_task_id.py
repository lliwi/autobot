"""run scheduled_task_id

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-05-30 00:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd3e4f5a6b7c8'
down_revision = 'c2d3e4f5a6b7'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('scheduled_task_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_runs_scheduled_task_id'), ['scheduled_task_id'], unique=False)
        batch_op.create_foreign_key(
            'fk_runs_scheduled_task_id', 'scheduled_tasks', ['scheduled_task_id'], ['id']
        )


def downgrade():
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.drop_constraint('fk_runs_scheduled_task_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_runs_scheduled_task_id'))
        batch_op.drop_column('scheduled_task_id')
