"""add is_featured and is_offer to products

Revision ID: e7f2a1b3c4d5
Revises: c32c9e6af771
Create Date: 2026-09-08

Phase 0 repair (2026-09-27)
---------------------------
This migration used to call `op.add_column` for `products.is_featured` and
`products.is_offer`, but the immediately preceding revision `c32c9e6af771`
already adds both columns. Any database built by running the chain from
scratch therefore died at this revision with:

    DuplicateColumn: column "is_featured" of relation "products" already exists

The intent of this revision was never to add the columns — `c32c9e6af771`
does that — it was to tighten them: NOT NULL, and drop the server default so
the ORM default (False) applies on insert. That is what `alter_column` below
does.

It only ever went unnoticed because the dev database was created with
`create_all()` from the models and then stamped, so the chain was never
executed end to end. No environment has applied this revision from a real
chain, so repairing it in place is safe.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e7f2a1b3c4d5"
down_revision: Union[str, None] = "c32c9e6af771"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_COLUMNS = ("is_featured", "is_offer")


def upgrade() -> None:
    # Columns already exist (added in c32c9e6af771) as nullable with a server
    # default of false. Tighten to NOT NULL and drop the default so the value
    # comes from the ORM on insert.
    for column in _COLUMNS:
        op.alter_column("products", column, nullable=False, server_default=None)


def downgrade() -> None:
    # Reverse of the above: back to nullable with a server default.
    # Deliberately NOT drop_column — this revision does not own these columns.
    for column in _COLUMNS:
        op.alter_column(
            "products", column, nullable=True, server_default=sa.false()
        )
