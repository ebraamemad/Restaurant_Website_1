"""customers, delivery zones, and order references

Revision ID: a1b2c3d4e5f6
Revises: d8e3c5b72a19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "d8e3c5b72a19"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("phone", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("first_order_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_order_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("phone"),
    )
    op.create_index("ix_customers_phone", "customers", ["phone"], unique=True)

    op.create_table(
        "delivery_zones",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("fee", sa.Numeric(precision=10, scale=2), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_delivery_zones_name", "delivery_zones", ["name"], unique=True)

    op.add_column("orders", sa.Column("customer_id", sa.Integer(), nullable=True))
    op.add_column("orders", sa.Column("delivery_zone_id", sa.Integer(), nullable=True))
    op.create_index("ix_orders_customer_id", "orders", ["customer_id"], unique=False)
    op.create_index("ix_orders_delivery_zone_id", "orders", ["delivery_zone_id"], unique=False)
    op.create_foreign_key("fk_orders_customer_id", "orders", "customers", ["customer_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_orders_delivery_zone_id", "orders", "delivery_zones", ["delivery_zone_id"], ["id"], ondelete="SET NULL")

    conn = op.get_bind()
    conn.execute(sa.text("""
        INSERT INTO customers (phone, name, first_order_at, last_order_at)
        SELECT normalized_phone,
               (array_agg(customer_name ORDER BY created_at))[1],
               min(created_at),
               max(created_at)
        FROM (
            SELECT regexp_replace(customer_phone, '\\D', '', 'g') AS normalized_phone,
                   customer_name,
                   created_at
            FROM orders
        ) old_orders
        WHERE normalized_phone <> ''
        GROUP BY normalized_phone
    """))
    conn.execute(sa.text("""
        UPDATE orders AS o
        SET customer_id = c.id
        FROM customers AS c
        WHERE c.phone = regexp_replace(o.customer_phone, '\\D', '', 'g')
    """))


def downgrade() -> None:
    op.drop_constraint("fk_orders_delivery_zone_id", "orders", type_="foreignkey")
    op.drop_constraint("fk_orders_customer_id", "orders", type_="foreignkey")
    op.drop_index("ix_orders_delivery_zone_id", table_name="orders")
    op.drop_index("ix_orders_customer_id", table_name="orders")
    op.drop_column("orders", "delivery_zone_id")
    op.drop_column("orders", "customer_id")
    op.drop_index("ix_delivery_zones_name", table_name="delivery_zones")
    op.drop_table("delivery_zones")
    op.drop_index("ix_customers_phone", table_name="customers")
    op.drop_table("customers")
