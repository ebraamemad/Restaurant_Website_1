"""add is_featured and is_offer to products

Revision ID: e7f2a1b3c4d5
Revises: c32c9e6af771
Create Date: 2026-09-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e7f2a1b3c4d5"
down_revision: Union[str, None] = "c32c9e6af771"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("products", sa.Column("is_featured", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("products", sa.Column("is_offer", sa.Boolean(), nullable=False, server_default="false"))
    op.alter_column("products", "is_featured", server_default=None)
    op.alter_column("products", "is_offer", server_default=None)


def downgrade() -> None:
    op.drop_column("products", "is_offer")
    op.drop_column("products", "is_featured")
