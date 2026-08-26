"""option groups, item notes, and option snapshots

Revision ID: 5f2d9a7c31e4
Revises: c7d9f2a41b36
Create Date: 2026-08-27

- add product_option_groups (required / allow_multiple / min-max selections per group)
- product_options.group_id -> product_option_groups.id (CASCADE)
- order_items.item_note (per-item customer note)
- add order_item_options snapshot (keeps selected options in order history)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5f2d9a7c31e4"
down_revision: Union[str, None] = "c7d9f2a41b36"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "product_option_groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("allow_multiple", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("min_selections", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_selections", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_available", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_product_option_groups_product_id"), "product_option_groups", ["product_id"], unique=False)

    op.add_column("product_options", sa.Column("group_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_product_options_group_id", "product_options", "product_option_groups",
        ["group_id"], ["id"], ondelete="CASCADE",
    )
    op.create_index(op.f("ix_product_options_group_id"), "product_options", ["group_id"], unique=False)

    op.add_column("order_items", sa.Column("item_note", sa.Text(), nullable=True))

    op.create_table(
        "order_item_options",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_item_id", sa.Integer(), nullable=False),
        sa.Column("option_id", sa.Integer(), nullable=True),
        sa.Column("group_name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("price_adjustment", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.ForeignKeyConstraint(["order_item_id"], ["order_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["option_id"], ["product_options.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_order_item_options_order_item_id"), "order_item_options", ["order_item_id"], unique=False)
    op.create_index(op.f("ix_order_item_options_option_id"), "order_item_options", ["option_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_order_item_options_option_id"), table_name="order_item_options")
    op.drop_index(op.f("ix_order_item_options_order_item_id"), table_name="order_item_options")
    op.drop_table("order_item_options")

    op.drop_column("order_items", "item_note")

    op.drop_index(op.f("ix_product_options_group_id"), table_name="product_options")
    op.drop_constraint("fk_product_options_group_id", "product_options", type_="foreignkey")
    op.drop_column("product_options", "group_id")

    op.drop_index(op.f("ix_product_option_groups_product_id"), table_name="product_option_groups")
    op.drop_table("product_option_groups")
