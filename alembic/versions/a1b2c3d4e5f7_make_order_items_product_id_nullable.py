"""make_order_items_product_id_nullable

Revision ID: a1b2c3d4e5f7
Revises: f1a2b3c4d5e6
Create Date: 2026-09-16 20:10:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f7"
down_revision: str = "f1a2b3c4d5e6"
branch_labels: list[str] | None = None
depends_on: list[str] | None = None


def upgrade() -> None:
    # Make order_items.product_id nullable so SET NULL FK works
    op.alter_column("order_items", "product_id", nullable=True)


def downgrade() -> None:
    op.alter_column("order_items", "product_id", nullable=False)