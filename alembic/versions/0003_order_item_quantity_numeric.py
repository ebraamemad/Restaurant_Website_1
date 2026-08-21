"""change order_items.quantity from integer to numeric(10,3)

Revision ID: 0003_quantity_numeric
Revises: 0002_addons
Create Date: 2026-08-21

The cart UI and OrderCreate schema allow fractional quantities
(e.g. 0.25 kg steps), but the column was created as INTEGER in
0001_initial, so PostgreSQL silently rounded every fractional
quantity. Align the column with the model (Numeric(10, 3)).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_quantity_numeric"
down_revision: Union[str, None] = "0002_addons"
branch_labels: Union[str, Sequence[str] | None] = None
depends_on: Union[str, Sequence[str] | None] = None


def upgrade() -> None:
    op.alter_column(
        "order_items",
        "quantity",
        existing_type=sa.Integer(),
        type_=sa.Numeric(10, 3),
        postgresql_using="quantity::numeric(10,3)",
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "order_items",
        "quantity",
        existing_type=sa.Numeric(10, 3),
        type_=sa.Integer(),
        postgresql_using="round(quantity)::integer",
        existing_nullable=False,
    )
