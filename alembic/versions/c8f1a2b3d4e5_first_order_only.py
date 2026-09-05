"""add first_order_only to coupons

Revision ID: c8f1a2b3d4e5
Revises: 9b7e4c2d15a8
Create Date: 2026-09-01

- coupons.first_order_only Boolean default false
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8f1a2b3d4e5"
down_revision: Union[str, None] = "9b7e4c2d15a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "coupons",
        sa.Column("first_order_only", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("coupons", "first_order_only")
