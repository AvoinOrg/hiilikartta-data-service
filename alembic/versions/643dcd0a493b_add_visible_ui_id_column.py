"""Add visible_ui_id column

Revision ID: 643dcd0a493b
Revises: 218b9d22b748
Create Date: 2024-03-21 14:49:15.340654

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '643dcd0a493b'
down_revision: Union[str, None] = '218b9d22b748'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('plan', sa.Column('visible_ui_id', sa.String(), nullable=True))
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('plan', 'visible_ui_id')
    # ### end Alembic commands ###