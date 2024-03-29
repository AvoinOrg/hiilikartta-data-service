"""Updgrade timestamps

Revision ID: 63fa38593e78
Revises: 
Create Date: 2024-03-15 16:11:13.855233

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '63fa38593e78'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column('plan', 'ui_id',
               existing_type=sa.UUID(),
               nullable=False)
    op.alter_column('plan', 'name',
               existing_type=sa.VARCHAR(),
               nullable=True)
    op.alter_column('plan', 'created_ts',
               existing_type=postgresql.TIMESTAMP(),
               nullable=False,
               existing_server_default=sa.text('CURRENT_TIMESTAMP(0)'))
    op.alter_column('plan', 'updated_ts',
               existing_type=postgresql.TIMESTAMP(),
               nullable=False,
               existing_server_default=sa.text('CURRENT_TIMESTAMP(0)'))
    op.alter_column('plan', 'last_index',
               existing_type=sa.INTEGER(),
               nullable=True)
    op.drop_index('idx_plan_ui_id', table_name='plan')
    op.drop_constraint('plan_ui_id_key', 'plan', type_='unique')
    op.create_index(op.f('ix_plan_ui_id'), 'plan', ['ui_id'], unique=False)
    op.create_unique_constraint('ui_id_unique', 'plan', ['ui_id'])
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint('ui_id_unique', 'plan', type_='unique')
    op.drop_index(op.f('ix_plan_ui_id'), table_name='plan')
    op.create_unique_constraint('plan_ui_id_key', 'plan', ['ui_id'])
    op.create_index('idx_plan_ui_id', 'plan', ['ui_id'], unique=False)
    op.alter_column('plan', 'last_index',
               existing_type=sa.INTEGER(),
               nullable=True)
    op.alter_column('plan', 'updated_ts',
               existing_type=postgresql.TIMESTAMP(),
               nullable=True,
               existing_server_default=sa.text('CURRENT_TIMESTAMP(0)'))
    op.alter_column('plan', 'created_ts',
               existing_type=postgresql.TIMESTAMP(),
               nullable=True,
               existing_server_default=sa.text('CURRENT_TIMESTAMP(0)'))
    op.alter_column('plan', 'name',
               existing_type=sa.VARCHAR(),
               nullable=True)
    op.alter_column('plan', 'ui_id',
               existing_type=sa.UUID(),
               nullable=True)
    # ### end Alembic commands ###
