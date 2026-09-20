"""change_orderitem_product_fk_to_set_null

Revision ID: f1a2b3c4d5e6
Revises: d44b83933e46
Create Date: 2026-09-16 20:00:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: str = "d44b83933e46"
branch_labels: list[str] | None = None
depends_on: list[str] | None = None


def upgrade() -> None:
    # Change order_items.product_id FK from ON DELETE RESTRICT to ON DELETE SET NULL
    # This allows deleting products while preserving order history (product_id becomes NULL)
    op.drop_constraint(
        "order_items_product_id_fkey",
        "order_items",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "order_items_product_id_fkey",
        "order_items",
        "products",
        ["product_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "order_items_product_id_fkey",
        "order_items",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "order_items_product_id_fkey",
        "order_items",
        "products",
        ["product_id"],
        ["id"],
        ondelete="RESTRICT",
    )