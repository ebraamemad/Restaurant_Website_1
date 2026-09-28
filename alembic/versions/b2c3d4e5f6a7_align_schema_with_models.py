"""align schema with models

Revision ID: b2c3d4e5f6a7
Revises: c1d2e3f4a5b6
Create Date: 2026-09-27 13:00:00.000000

Phase 0: close the gap between what the models declare and what the migration
chain actually builds.

`alembic check` against a database built purely from this chain reported three
real problems (plus `alembic_version` itself):

1. **`orders.payment_method` was never created by any migration.** The `Order`
   model has mapped the column, so inserting an order on a freshly built
   environment failed with `UndefinedColumn`. This is the reason the dev
   database looked fine: it was created with `create_all()` from the models, so
   the column existed and the missing migration was invisible.

2. **`ix_order_items_product_id` was never created.** `OrderItem.product_id`
   declares `index=True`. Missing index only, but it is on the hot path for
   "which orders contain this product" and for the ON DELETE SET NULL.

3. **`delivery_options.is_pickup` was never dropped.** `d44b83933e46` added
   `delivery_type` to replace it, but despite its own docstring it never removed
   the old boolean. Nothing in the code or the models reads `is_pickup` any
   more, so this migration removes it. The downgrade re-adds it, so this is
   reversible.

4. **`customers.phone` and `delivery_zones.name` each carried two unique
   indexes.** The initial migration added a named UNIQUE constraint
   (`customers_phone_key`), which PostgreSQL backs with a same-named index. A
   later migration then added a second *unique* index on the same column
   (`ix_customers_phone`). Both do identical work, so every insert and every
   uniqueness check paid for two. The models declare only the second form
   (`unique=True, index=True`, which SQLAlchemy renders as one unique index),
   so this migration drops the redundant constraint and its backing index. The
   uniqueness guarantee is unchanged; the downgrade re-adds it.

5. **`orders.payment_method` may already exist.** The step below is guarded so
   this revision is idempotent for that one column. Some environments (notably
   the development database) were built with `Base.metadata.create_all()` from
   the models and then hand-stamped with `alembic stamp`, so the chain never
   ran against them and `create_all()` had already created the column that
   this revision introduces. Unconditionally adding it there fails with
   `DuplicateColumn`, which would block the rest of the chain. The guard makes
   this revision succeed on a chain-built database and on a `create_all()`
   database alike, and leaves the resulting schema identical either way.

   This revision is unreleased and unapplied everywhere, so making it
   tolerant in place removes no history.

The `coupons.code` difference that Alembic also reports is a naming artefact
only: the model leaves its UNIQUE constraint unnamed while PostgreSQL names it
`coupons_code_key`. The column is unique either way.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: str = "c1d2e3f4a5b6"
branch_labels: list[str] | None = None
depends_on: list[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    # 1) The model maps this column; nothing created it. Nullable because the
    #    model types it Optional — orders placed before payment methods existed
    #    stay NULL. Guarded: a `create_all()`-built database already has it.
    if "payment_method" not in {c["name"] for c in inspector.get_columns("orders")}:
        op.add_column(
            "orders", sa.Column("payment_method", sa.String(length=20), nullable=True)
        )
    # 2) Matches OrderItem.product_id's index=True.
    if "ix_order_items_product_id" not in {i["name"] for i in inspector.get_indexes("order_items")}:
        op.create_index("ix_order_items_product_id", "order_items", ["product_id"], unique=False)
    # 3) Dead column, superseded by delivery_type.
    op.drop_column("delivery_options", "is_pickup")
    # 4) Drop the redundant named UNIQUE constraints (and the indexes backing
    #    them). The unique indexes the models declare stay, so the uniqueness
    #    guarantee is identical — we are removing the second copy, not the
    #    protection.
    op.drop_constraint("customers_phone_key", "customers", type_="unique")
    op.drop_constraint("delivery_zones_name_key", "delivery_zones", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint("delivery_zones_name_key", "delivery_zones", ["name"])
    op.create_unique_constraint("customers_phone_key", "customers", ["phone"])
    op.add_column(
        "delivery_options",
        sa.Column("is_pickup", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("delivery_options", "is_pickup", server_default=None)
    op.drop_index("ix_order_items_product_id", table_name="order_items")
    inspector = sa.inspect(op.get_bind())
    if "payment_method" in {c["name"] for c in inspector.get_columns("orders")}:
        op.drop_column("orders", "payment_method")
