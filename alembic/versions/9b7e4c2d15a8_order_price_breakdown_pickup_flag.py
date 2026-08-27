"""order price breakdown and pickup flag

Revision ID: 9b7e4c2d15a8
Revises: 5f2d9a7c31e4
Create Date: 2026-08-27

- orders: save subtotal / delivery_fee / discount_amount snapshots (final calc lives in backend)
- delivery_options: is_pickup to distinguish pickup (no address/fee) from delivery
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9b7e4c2d15a8"
down_revision: Union[str, None] = "5f2d9a7c31e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("subtotal", sa.Numeric(precision=10, scale=2), nullable=False, server_default="0"))
    op.add_column("orders", sa.Column("delivery_fee", sa.Numeric(precision=10, scale=2), nullable=False, server_default="0"))
    op.add_column("orders", sa.Column("discount_amount", sa.Numeric(precision=10, scale=2), nullable=False, server_default="0"))
    op.add_column("delivery_options", sa.Column("is_pickup", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("delivery_options", "is_pickup")
    op.drop_column("orders", "discount_amount")
    op.drop_column("orders", "delivery_fee")
    op.drop_column("orders", "subtotal")
