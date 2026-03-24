"""add forestry_scenario to plan

Revision ID: 8b0b9f4b8d6e
Revises: 643dcd0a493b
Create Date: 2026-03-10 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8b0b9f4b8d6e"
down_revision: Union[str, None] = "643dcd0a493b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "plan",
        sa.Column("forestry_scenario", sa.Integer(), nullable=True, server_default="1"),
    )
    op.execute("UPDATE plan SET forestry_scenario = 1 WHERE forestry_scenario IS NULL")
    op.alter_column("plan", "forestry_scenario", nullable=False, server_default="1")


def downgrade() -> None:
    op.drop_column("plan", "forestry_scenario")
