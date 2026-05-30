"""scheduled_task schedule_config

Revision ID: c2d3e4f5a6b7
Revises: b1a2c3d4e5f6
Create Date: 2026-05-30 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c2d3e4f5a6b7'
down_revision = 'b1a2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('scheduled_tasks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('schedule_config', sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table('scheduled_tasks', schema=None) as batch_op:
        batch_op.drop_column('schedule_config')
