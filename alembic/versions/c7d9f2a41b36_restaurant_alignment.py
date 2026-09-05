"""restaurant alignment: consolidate settings, integer quantity, proper coupon/delivery FKs

Revision ID: c7d9f2a41b36
Revises: 852de6ed0909
Create Date: 2026-08-26

- drop store_settings (superseded by restaurant_settings)
- order_items.quantity numeric(10,3) -> integer (restaurant = piece counts)
- orders.coupon_code (FK to coupons.code) -> orders.coupon_id (FK to coupons.id, SET NULL)
- orders.delivery_option_id: add ON DELETE SET NULL
- order_item_addons.addon_id: nullable + ON DELETE SET NULL (name/price snapshot keeps history)
"""
from typing import List, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7d9f2a41b36"
down_revision: Union[str, None] = "852de6ed0909"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _fk_constraints(conn, table: str, column: str) -> List[str]:
    rows = conn.execute(
        sa.text(
            """
            SELECT conname FROM pg_constraint
            WHERE conrelid = to_regclass(:tbl) AND contype = 'f'
              AND conkey[1] = (
                  SELECT attnum FROM pg_attribute
                  WHERE attrelid = to_regclass(:tbl) AND attname = :col
              )
            """
        ),
        {"tbl": table, "col": column},
    ).fetchall()
    return [row[0] for row in rows]


def upgrade() -> None:
    conn = op.get_bind()

    # 1) settings consolidation
    op.drop_table("store_settings")

    # 2) integer quantities
    op.alter_column(
        "order_items",
        "quantity",
        existing_type=sa.Numeric(precision=10, scale=3),
        type_=sa.Integer(),
        existing_nullable=False,
        postgresql_using="round(quantity)::integer",
    )

    # 3) orders.coupon_code -> orders.coupon_id
    for name in _fk_constraints(conn, "orders", "coupon_code"):
        op.drop_constraint(name, "orders", type_="foreignkey")
    op.drop_column("orders", "coupon_code")
    op.add_column("orders", sa.Column("coupon_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_orders_coupon_id"), "orders", ["coupon_id"], unique=False)
    op.create_foreign_key(
        "fk_orders_coupon_id", "orders", "coupons",
        ["coupon_id"], ["id"], ondelete="SET NULL",
    )

    # 4) delivery option reference survives deletion of the option
    for name in _fk_constraints(conn, "orders", "delivery_option_id"):
        op.drop_constraint(name, "orders", type_="foreignkey")
    op.create_foreign_key(
        "fk_orders_delivery_option_id", "orders", "delivery_options",
        ["delivery_option_id"], ["id"], ondelete="SET NULL",
    )

    # 5) addon history reference survives deletion of the addon
    for name in _fk_constraints(conn, "order_item_addons", "addon_id"):
        op.drop_constraint(name, "order_item_addons", type_="foreignkey")
    op.alter_column("order_item_addons", "addon_id", existing_type=sa.Integer(), nullable=True)
    op.create_foreign_key(
        "fk_order_item_addons_addon_id", "order_item_addons", "addons",
        ["addon_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    conn = op.get_bind()

    op.drop_constraint("fk_order_item_addons_addon_id", "order_item_addons", type_="foreignkey")
    op.alter_column("order_item_addons", "addon_id", existing_type=sa.Integer(), nullable=False)
    op.create_foreign_key(
        "order_item_addons_addon_id_fkey", "order_item_addons", "addons",
        ["addon_id"], ["id"],
    )

    op.drop_constraint("fk_orders_delivery_option_id", "orders", type_="foreignkey")
    op.create_foreign_key(
        "orders_delivery_option_id_fkey", "orders", "delivery_options",
        ["delivery_option_id"], ["id"],
    )

    op.drop_constraint("fk_orders_coupon_id", "orders", type_="foreignkey")
    op.drop_index(op.f("ix_orders_coupon_id"), table_name="orders")
    op.drop_column("orders", "coupon_id")
    op.add_column("orders", sa.Column("coupon_code", sa.String(length=50), nullable=True))
    op.create_foreign_key(
        "orders_coupon_code_fkey", "orders", "coupons",
        ["coupon_code"], ["code"],
    )

    op.alter_column(
        "order_items",
        "quantity",
        existing_type=sa.Integer(),
        type_=sa.Numeric(precision=10, scale=3),
        existing_nullable=False,
        postgresql_using="quantity::numeric(10,3)",
    )

    op.create_table(
        "store_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("store_name", sa.String(length=160), nullable=False),
        sa.Column("whatsapp_number", sa.String(length=30), nullable=False),
        sa.Column("phone_number", sa.String(length=30), nullable=True),
        sa.Column("address", sa.String(length=300), nullable=True),
        sa.Column("logo_url", sa.String(length=500), nullable=True),
        sa.Column("banner_url", sa.String(length=500), nullable=True),
        sa.Column("working_hours", sa.String(length=250), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
