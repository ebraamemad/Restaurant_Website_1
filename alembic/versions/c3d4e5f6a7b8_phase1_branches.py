"""Phase 1: branches, branch-aware orders, order channel, ordering hours

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-27 18:00:00.000000

## Phase 1 — Database & Multi-Branch Foundation

This migration is additive. It creates new tables/columns and backfills
existing rows. It does not delete, rewrite, or recreate any order,
customer, or product row.

### 1. `branches`

A small table for the restaurant's 1–3 branches: name, address, phone,
opening hours (informational only), and the two flags the app needs.

`is_default` marks the single fallback branch. The partial unique index
`ix_branches_single_default` makes "at most one default branch" a
database guarantee rather than an application convention, so a race
between two admin requests cannot produce two defaults.

`opening_hours` here is **display only**. Online ordering hours are global
for the whole restaurant (see §4) — no business logic reads the branch
column.

### 2. `orders.branch_id`

Every order must belong to a branch, so the column ends up `NOT NULL`.
The upgrade is written as add-nullable → backfill → set NOT NULL, because
`ALTER TABLE ... SET NOT NULL` on a populated table would fail without
the backfill.

The backfill assigns **every** pre-existing order to the default branch,
so no historical order is lost or orphaned. The FK is `ON DELETE RESTRICT`
on purpose: a branch that still has orders cannot be deleted, which keeps
order history intact. Branches are deactivated instead.

The upgrade also creates a partial index for the same reason as above:
at most one default branch, enforced by the database.

### 3. `orders.channel`

`system` (ordered on the site, seen in the dashboard) or `whatsapp`
(ordered on the site, then handed off to WhatsApp). Both create the same
`Order` row — the column only records how the order was initiated.

`server_default='whatsapp'` is deliberate: every order that existed
before this migration went through the WhatsApp redirect, so
`whatsapp` is the historically accurate value for the backfilled rows.
The server default is left in place so raw inserts that omit the column
still get a valid value.

A CHECK constraint keeps the column to the two supported values instead of
letting a typo become a third channel that no query knows how to read.

Note: `orders.status` is intentionally left as a free `String(30)` with
no CHECK. Phase 2 adds preparing / ready / out_for_delivery / completed /
cancelled, and constraining the column now would mean altering the
constraint in that migration for no benefit.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str = "b2c3d4e5f6a7"
branch_labels: list[str] | None = None
depends_on: list[str] | None = None


# ثابت مش متغيّر بين التشغيلات: لو الـmigration اتشغّل بالغلط تاني،
# الكود ده بيعرف يلقى الفرع بدل ما يعمل واحد جديد.
DEFAULT_BRANCH_NAME = "الفرع الرئيسي"
DEFAULT_BRANCH_PHONE = "01000000000"
DEFAULT_BRANCH_HOURS = "10:00 — 02:00"


def upgrade() -> None:
    bind = op.get_bind()

    # 1) جدول الفروع.
    op.create_table(
        "branches",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("address", sa.String(length=300), nullable=True),
        sa.Column("phone", sa.String(length=30), nullable=True),
        sa.Column("opening_hours", sa.String(length=250), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_branches_is_active", "branches", ["is_active"], unique=False)

    # 2) فرع افتراضي واحد بس. الفحص على عدد الصفوف موجود عشان لو الـ
    #    migration اتشغّلت تاني بالغلط ما نعملش فرع تاني.
    existing = bind.execute(sa.text("SELECT count(*) FROM branches")).scalar_one()
    if existing == 0:
        bind.execute(
            sa.text(
                """
                INSERT INTO branches (name, address, phone, opening_hours,
                                      is_active, is_default)
                VALUES (:name, NULL, :phone, :hours, true, true)
                """
            ),
            {
                "name": DEFAULT_BRANCH_NAME,
                "phone": DEFAULT_BRANCH_PHONE,
                "hours": DEFAULT_BRANCH_HOURS,
            },
        )

    # 3) orders.branch_id — إضافة فاضية، ثم تعبئة، ثم NOT NULL.
    op.add_column("orders", sa.Column("branch_id", sa.Integer(), nullable=True))

    default_branch_id = bind.execute(
        sa.text("SELECT id FROM branches WHERE is_default IS TRUE ORDER BY id LIMIT 1")
    ).scalar_one()
    # كل الطلبات القديمة بتروح على الفرع الافتراضي. مفيش DELETE ولا
    # UPDATE على أي صف تاني، والطلبات نفسها ما بتتلمسش غير في العمود ده.
    bind.execute(
        sa.text("UPDATE orders SET branch_id = :branch_id WHERE branch_id IS NULL"),
        {"branch_id": default_branch_id},
    )

    op.create_index("ix_orders_branch_id", "orders", ["branch_id"], unique=False)
    op.create_foreign_key(
        "fk_orders_branch_id_branches",
        "orders",
        "branches",
        ["branch_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.alter_column("orders", "branch_id", nullable=False)

    # 4) orders.channel — كل طلبات V1 كانت بتروح على واتساب، فده القيمة
    #    التاريخية الصح للصفوف الموجودة.
    op.add_column(
        "orders",
        sa.Column(
            "channel", sa.String(length=20), nullable=False, server_default="whatsapp"
        ),
    )
    op.create_index("ix_orders_channel", "orders", ["channel"], unique=False)
    op.create_check_constraint(
        "ck_orders_channel", "orders", sa.text("channel IN ('system', 'whatsapp')")
    )

    # 5) ساعات استقبال الطلبات —.globale على مستوى المطعم كله.
    #    NULL/NULL = مفتوح طول الوقت، وده سلوك V1 الحالي.
    op.add_column(
        "restaurant_settings", sa.Column("ordering_open_time", sa.Time(), nullable=True)
    )
    op.add_column(
        "restaurant_settings", sa.Column("ordering_close_time", sa.Time(), nullable=True)
    )

    # 6) "فرع افتراضي واحد بس" كضمان في الداتابيز مش مجرد اتفاق في الكود.
    op.create_index(
        "ix_branches_single_default",
        "branches",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default IS TRUE"),
    )


def downgrade() -> None:
    op.drop_index("ix_branches_single_default", table_name="branches")
    op.drop_column("restaurant_settings", "ordering_close_time")
    op.drop_column("restaurant_settings", "ordering_open_time")
    op.drop_constraint("ck_orders_channel", "orders", type_="check")
    op.drop_index("ix_orders_channel", table_name="orders")
    op.drop_column("orders", "channel")
    op.drop_constraint("fk_orders_branch_id_branches", "orders", type_="foreignkey")
    op.drop_index("ix_orders_branch_id", table_name="orders")
    op.alter_column("orders", "branch_id", nullable=True)
    op.drop_column("orders", "branch_id")
    op.drop_index("ix_branches_is_active", table_name="branches")
    op.drop_table("branches")
