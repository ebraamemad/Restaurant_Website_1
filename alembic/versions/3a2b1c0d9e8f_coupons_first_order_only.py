"""first-order-only coupon validation support

Revision ID: 3a2b1c0d9e8f
Revises: a1b2c3d4e5f6
Create Date: 2026-09-03

- Schema change (coupons.first_order_only) was already applied by c8f1a2b3d4e5.
- This revision is a no-op marker so the chain resolves to a single head.
"""
from typing import Sequence, Union


revision: str = "3a2b1c0d9e8f"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
