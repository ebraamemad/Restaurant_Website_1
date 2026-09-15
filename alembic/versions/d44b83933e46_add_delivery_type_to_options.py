"""add_delivery_type_to_options

Revision ID: d44b83933e46
Revises: e7f2a1b3c4d5
Create Date: 2026-09-16 01:09:43.407134

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd44b83933e46'
down_revision: Union[str, None] = 'e7f2a1b3c4d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("delivery_options", sa.Column("delivery_type", sa.String(length=20), nullable=False, server_default="fixed"))
    op.alter_column("delivery_options", "delivery_type", server_default=None)


def downgrade() -> None:
    op.drop_column("delivery_options", "delivery_type")
