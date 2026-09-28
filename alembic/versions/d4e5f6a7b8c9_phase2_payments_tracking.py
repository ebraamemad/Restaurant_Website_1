"""Phase 2: payment methods, order status flow, tracking tokens, delivery snapshots

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-28 10:00:00.000000

## Phase 2 — Payments, Order Flow, Tracking

This migration is **additive and idempotent**. It creates what is missing and
adopts what a previous (lost) 2026-09-21 change already left in some
databases. It never drops a table, column, or row that an older revision
created.

### Why the idempotence

`restaurant_store` (development) already contains `payment_methods`,
`push_subscriptions`, `order_push_subscriptions`, `orders.payment_method_id`
and `orders.order_tracking_hash` from work that was never committed, while
`restaurant_store_test` (built purely from migrations) has none of them. A
plain `create_table` would fail on the first and be wrong on the second. So
each object is checked with the inspector first, and the correct branch —
create or adopt — is taken. The end state is identical in both databases,
which is the point `tools/schema_diff.py` verifies.

### 1. `payment_methods`

Small admin-managed table: `code` (unique, referenced by orders),
`name`, `description`, and `kind` (`offline` / `online`). `kind` is
documentation for the UI only — no payment gateway is implemented in Phase 2,
and there is deliberately no column for a card number, CVV, PIN, or any bank
credential.

The four default rows are seeded only when the table is empty, so a fresh
install has Cash/Card/E-Wallet/Bank Transfer available and an existing
database is never given duplicates.

### 2. `orders` — payment columns

`payment_method_id` is a nullable `ON DELETE SET NULL` FK: deactivating or
deleting a payment method must not delete order history.

`payment_status` is `NOT NULL DEFAULT 'pending'`. Existing rows are all set
to `pending` by the default, which is the truthful value — V1 recorded no
payment state at all, so claiming `paid` would be inventing data.

The snapshot columns (`payment_method_code`, `payment_method_label`,
`payment_instructions`) are backfilled from the linked `payment_methods` row
so old orders render the label they had, not whatever the label is today.
The V1 free-text `orders.payment_method` column is left untouched; it stays
as history and the new columns are authoritative going forward.

### 3. `orders` — status normalisation

`status` had no CHECK constraint (Phase 1 deliberately left it free so
Phase 2 could define the full set). Two rows in `restaurant_store` hold
`status = 'new'`, a value from the lost 2026-09-21 work that the admin
status dropdown cannot display. `new` is mapped to `pending` — the value
that means "received, not yet acted on" — before the constraint is added,
so the migration cannot fail and the dashboard stops rendering a dead status.

`delivered` is deliberately **kept** in the constraint alongside `completed`:
it is a real V1 terminal status with rows in the database, and dropping it
would destroy history. Both are terminal; see `OrderStatus.is_terminal`.

### 4. `orders` — tracking

`order_tracking_hash` holds the **SHA-256 of a random 32-byte token**, never
the token itself. The token is returned to the customer once at order
creation and kept in their browser; the database only stores the hash, so a
database leak does not yield working tracking links. The column is indexed
because every tracking request is a hash lookup.

Existing rows are left `NULL`: their tokens were never issued, so there is
nothing truthful to backfill. Those orders are not reachable via tracking
until the customer places a new one.

### 5. `orders` — delivery snapshots and pending fee

`delivery_fee_pending` defaults to `false`. It is set to `true` only for new
`call_center` orders, where the fee is genuinely not known yet. Existing
orders keep `false` because their `delivery_fee` was already resolved.

The `*_label` / `*_snapshot` columns are left `NULL` for existing rows: the
names they had at order time were never recorded, and inventing values from
today's configuration would be a lie in an audit field. New orders fill them
in at creation time.

### 6. `restaurant_settings` — PWA naming and reserved VAPID fields

`pwa_name` / `pwa_short_name` / `pwa_icon_url` are used by the manifest
endpoint; it falls back to the restaurant name when they are empty, which
keeps V1 behaviour.

The widths (160 / 80) match the columns that already exist in the development
database, so the adoption path here does not silently narrow them and force a
second `modify_type` migration later. `short_name` is still capped at 12
characters in `save_settings` and in the manifest, because that is the browser
limit for the installed-app label — the column is only 80 wide to leave room.

`vapid_private_pem` / `vapid_public_pem` are **reserved for a later phase**.
They are adopted here purely to stop schema drift and are not read or
written by any Phase 2 code.

### 7. Push tables — declared, not used

`push_subscriptions` and `order_push_subscriptions` are adopted/created so the
schema is declared in both models and migrations. **No Phase 2 code reads or
writes them, and no notification is sent.** They are the foundation for a
later notifications phase.

### 8. Legacy `destination` columns

`orders.destination` and `restaurant_settings.order_destination` are orphans
from the lost 2026-09-21 work. `orders.channel` (Phase 1) already means the
same thing and is referenced by code.

Rather than dropping them blind, the upgrade copies the one piece of
information they still hold that `channel` does not: `destination =
'internal'` becomes `channel = 'system'`, because an internal order is
exactly what `system` means. After that copy, `destination` is redundant and
is dropped. The 12 existing orders all keep their row and their meaning; the
3 internal ones are simply re-labelled with the canonical vocabulary.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str = "c3d4e5f6a7b8"
branch_labels: list[str] | None = None
depends_on: list[str] | None = None


# --------------------------------------------------------------------------
# Idempotence helpers
# --------------------------------------------------------------------------
# كل object بيتشيك عليه الـinspector الأول. "موجود" = نتبنّاه ونعدّلوش،
# "مش موجود" = ننشئه. النتيجة النهائية واحدة في أي داتابيز.
def _table_exists(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _column_names(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _has_column(table: str, column: str) -> bool:
    return column in _column_names(table)


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    if not _has_column(table, column.name):
        op.add_column(table, column)


def _index_names(table: str) -> set[str]:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def _create_index_if_missing(name: str, table: str, columns: list[str], unique: bool = False) -> None:
    if name not in _index_names(table):
        op.create_index(name, table, columns, unique=unique)


def _check_constraint_names(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {
        c["name"]
        for c in inspector.get_check_constraints(table)
        if c.get("name")
    }


def _create_check_if_missing(name: str, table: str, condition: str) -> None:
    if name not in _check_constraint_names(table):
        op.create_check_constraint(name, table, sa.text(condition))


def _fk_names(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {fk["name"] for fk in inspector.get_foreign_keys(table) if fk.get("name")}


def _create_fk_if_missing(
    name: str, table: str, referent: str, local_cols: list[str], remote_cols: list[str], ondelete: str
) -> None:
    if name not in _fk_names(table):
        op.create_foreign_key(name, table, referent, local_cols, remote_cols, ondelete=ondelete)


# The full order status set. `delivered` stays because V1 orders carry it and
# it is a real terminal state; `new` is deliberately absent — the upgrade
# below rewrites those rows to `pending` before the constraint is created.
ORDER_STATUSES = (
    "'pending', 'confirmed', 'preparing', 'ready', "
    "'out_for_delivery', 'completed', 'delivered', 'cancelled'"
)
PAYMENT_STATUSES = "'pending', 'confirmed', 'paid', 'failed'"
DELIVERY_TYPES = "'pickup', 'fixed', 'zone', 'call_center'"
PAYMENT_KINDS = "'offline', 'online'"

# Default payment methods, seeded only into an empty table. Names and
# descriptions are in Arabic to match the rest of the admin UI.
DEFAULT_PAYMENT_METHODS = [
    ("cash", "كاش", "الدفع نقدًا عند الاستلام/التوصيل", "offline", 1),
    ("card", "فيزا", "الدفع بالبطاقة البنكية", "offline", 2),
    ("ewallet", "محفظة إلكترونية", "الدفع عبر محافظ الهاتف", "online", 3),
    ("instapay", "انستاباي", "الدفع عبر انستاباي", "online", 4),
]


def _seed_payment_methods(bind) -> None:
    count = bind.execute(sa.text("SELECT count(*) FROM payment_methods")).scalar_one()
    if count:
        return
    for code, name, description, kind, sort_order in DEFAULT_PAYMENT_METHODS:
        bind.execute(
            sa.text(
                """
                INSERT INTO payment_methods (code, name, description, kind, sort_order)
                VALUES (:code, :name, :description, :kind, :sort_order)
                """
            ),
            {
                "code": code,
                "name": name,
                "description": description,
                "kind": kind,
                "sort_order": sort_order,
            },
        )


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # 1. payment_methods
    # ------------------------------------------------------------------
    if not _table_exists("payment_methods"):
        op.create_table(
            "payment_methods",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("code", sa.String(length=20), nullable=False),
            sa.Column("name", sa.String(length=80), nullable=False),
            sa.Column("description", sa.String(length=300), nullable=True),
            sa.Column(
                "kind",
                sa.String(length=10),
                nullable=False,
                server_default="offline",
            ),
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
            sa.Column(
                "sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")
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
    _create_index_if_missing(
        "ix_payment_methods_code", "payment_methods", ["code"], unique=True
    )
    _create_check_if_missing(
        "ck_payment_methods_kind", "payment_methods", f"kind IN ({PAYMENT_KINDS})"
    )
    _seed_payment_methods(bind)

    # ------------------------------------------------------------------
    # 2. orders — payment
    # ------------------------------------------------------------------
    _add_column_if_missing(
        "orders", sa.Column("payment_method_id", sa.Integer(), nullable=True)
    )
    _create_index_if_missing(
        "ix_orders_payment_method_id", "orders", ["payment_method_id"]
    )
    # SET NULL, not CASCADE: removing a payment method must never remove the
    # orders that used it. The label snapshot below keeps them readable.
    _create_fk_if_missing(
        "fk_orders_payment_method_id_payment_methods",
        "orders",
        "payment_methods",
        ["payment_method_id"],
        ["id"],
        "SET NULL",
    )

    _add_column_if_missing(
        "orders", sa.Column("payment_method_code", sa.String(length=20), nullable=True)
    )
    _add_column_if_missing(
        "orders", sa.Column("payment_method_label", sa.String(length=120), nullable=True)
    )
    _add_column_if_missing(
        "orders", sa.Column("payment_instructions", sa.String(length=300), nullable=True)
    )
    _add_column_if_missing(
        "orders",
        sa.Column(
            "payment_status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
    )
    _create_index_if_missing("ix_orders_payment_status", "orders", ["payment_status"])
    _add_column_if_missing(
        "orders", sa.Column("payment_reference", sa.String(length=120), nullable=True)
    )
    _add_column_if_missing(
        "orders", sa.Column("payment_confirmed_at", sa.DateTime(timezone=True), nullable=True)
    )
    _create_check_if_missing(
        "ck_orders_payment_status", "orders", f"payment_status IN ({PAYMENT_STATUSES})"
    )

    # لقطة الاسم/التعليمات من جدول طرق الدفع للطلبات اللي مربوطة بيه.
    # الطلبات القديمة تشوف الاسم اللي كان شايفه العميل وقتها، مش الاسم
    # الحالي بعد تعديل الأدمن. الطلبات اللي مالهاش payment_method_id
    # (طلبات V1) بتفضل فاضية — مفيش رابط نتكلم عنه.
    bind.execute(
        sa.text(
            """
            UPDATE orders o
               SET payment_method_code = p.code,
                   payment_method_label = p.name,
                   payment_instructions = p.description
              FROM payment_methods p
             WHERE p.id = o.payment_method_id
               AND (o.payment_method_code IS NULL
                    OR o.payment_method_label IS NULL
                    OR o.payment_instructions IS NULL)
            """
        )
    )

    # ------------------------------------------------------------------
    # 3. orders — status
    # ------------------------------------------------------------------
    # `new` كانت في داتابيز التطوير من شغل ضاع مصدره، واللوحة التحكم مش
    # بتعرف تقراها. `pending` هي المعنى الصح: وصل الطلب ومحدش تصرف فيه بعد.
    # ده UPDATE على عمود واحد بس — مفيش صف بيتحذف ولا يتغير غير ده.
    bind.execute(
        sa.text("UPDATE orders SET status = 'pending' WHERE status = 'new'")
    )
    _create_check_if_missing(
        "ck_orders_status", "orders", f"status IN ({ORDER_STATUSES})"
    )

    # ------------------------------------------------------------------
    # 4. orders — tracking
    # ------------------------------------------------------------------
    # العمود ده هاش مش رمز. الـtoken نفسه بيرجع للعميل مرة واحدة وقت
    # إنشاء الطلب، واللي بيتخزّن هنا SHA-256 بتاعه. الطلبات الموجودة
    # بتفضل NULL: مفيش token اتصدر ليها أصلاً، ومفيش حاجة truthful
    # نملاها بيها.
    _add_column_if_missing(
        "orders", sa.Column("order_tracking_hash", sa.String(length=64), nullable=True)
    )
    _create_index_if_missing("ix_orders_order_tracking_hash", "orders", ["order_tracking_hash"])

    # ------------------------------------------------------------------
    # 5. orders — delivery snapshots + pending fee
    # ------------------------------------------------------------------
    _add_column_if_missing(
        "orders",
        sa.Column(
            "delivery_fee_pending",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    _add_column_if_missing(
        "orders", sa.Column("delivery_method_label", sa.String(length=150), nullable=True)
    )
    _add_column_if_missing(
        "orders", sa.Column("delivery_zone_label", sa.String(length=120), nullable=True)
    )
    _add_column_if_missing(
        "orders", sa.Column("delivery_estimated_time", sa.String(length=50), nullable=True)
    )
    _add_column_if_missing(
        "orders", sa.Column("delivery_type_snapshot", sa.String(length=20), nullable=True)
    )
    _add_column_if_missing(
        "orders", sa.Column("branch_name_snapshot", sa.String(length=160), nullable=True)
    )

    _create_check_if_missing(
        "ck_delivery_options_delivery_type",
        "delivery_options",
        f"delivery_type IN ({DELIVERY_TYPES})",
    )

    # ------------------------------------------------------------------
    # 6. restaurant_settings — PWA naming + reserved VAPID
    # ------------------------------------------------------------------
    _add_column_if_missing(
        "restaurant_settings", sa.Column("pwa_name", sa.String(length=160), nullable=True)
    )
    _add_column_if_missing(
        "restaurant_settings", sa.Column("pwa_short_name", sa.String(length=80), nullable=True)
    )
    _add_column_if_missing(
        "restaurant_settings", sa.Column("pwa_icon_url", sa.String(length=500), nullable=True)
    )
    # محجوزين لمرحلة الإشعارات الجاية. مفيش كود في المرحلة دي بيقراهم.
    _add_column_if_missing(
        "restaurant_settings", sa.Column("vapid_private_pem", sa.Text(), nullable=True)
    )
    _add_column_if_missing(
        "restaurant_settings", sa.Column("vapid_public_pem", sa.Text(), nullable=True)
    )

    # ------------------------------------------------------------------
    # 7. Push tables — declared for a later phase, unused now
    # ------------------------------------------------------------------
    if not _table_exists("push_subscriptions"):
        op.create_table(
            "push_subscriptions",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("endpoint", sa.String(length=600), nullable=False),
            sa.Column("p256dh", sa.String(length=300), nullable=False),
            sa.Column("auth", sa.String(length=200), nullable=False),
            sa.Column("user_agent", sa.String(length=400), nullable=True),
            sa.Column("device", sa.String(length=40), nullable=True),
            sa.Column("customer_id", sa.Integer(), nullable=True),
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
    _create_index_if_missing(
        "ix_push_subscriptions_endpoint", "push_subscriptions", ["endpoint"], unique=True
    )
    _create_index_if_missing(
        "ix_push_subscriptions_customer_id", "push_subscriptions", ["customer_id"]
    )
    _create_fk_if_missing(
        "fk_push_subscriptions_customer_id",
        "push_subscriptions",
        "customers",
        ["customer_id"],
        ["id"],
        "SET NULL",
    )

    if not _table_exists("order_push_subscriptions"):
        op.create_table(
            "order_push_subscriptions",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("order_id", sa.Integer(), nullable=False),
            sa.Column("subscription_id", sa.Integer(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
    _create_index_if_missing(
        "ix_order_push_subscriptions_order_id", "order_push_subscriptions", ["order_id"]
    )
    _create_index_if_missing(
        "ix_order_push_subscriptions_subscription_id",
        "order_push_subscriptions",
        ["subscription_id"],
    )
    _create_fk_if_missing(
        "fk_order_push_subscriptions_order_id",
        "order_push_subscriptions",
        "orders",
        ["order_id"],
        ["id"],
        "CASCADE",
    )
    _create_fk_if_missing(
        "fk_order_push_subscriptions_subscription_id",
        "order_push_subscriptions",
        "push_subscriptions",
        ["subscription_id"],
        ["id"],
        "CASCADE",
    )

    # ------------------------------------------------------------------
    # 8. Legacy `destination` columns
    # ------------------------------------------------------------------
    # `orders.channel` (Phase 1) بيعني نفس الحاجة بالظبط، بس `destination`
    # لسه فيه معلومة واحدة `channel` مش بتعرفها: قيمة `internal` معناها
    # طلب جه من جوه النظام مش من واتساب — وده حرفياً معنى `system`.
    # بننسخ المعلومة دي الأول وبعدين نطير العمود الميت.
    if _has_column("orders", "destination"):
        bind.execute(
            sa.text(
                """
                UPDATE orders
                   SET channel = 'system'
                 WHERE destination = 'internal'
                   AND channel = 'whatsapp'
                """
            )
        )
        op.drop_column("orders", "destination")

    # إعداد عام للمطعم كله اتسجل في experiment ضاع. مفيش شاشة في الأدمن
    # بتقرأه، وقناة الطلب بتتحدد لكل طلب. مفيش معلومة بتضيع.
    if _has_column("restaurant_settings", "order_destination"):
        op.drop_column("restaurant_settings", "order_destination")


def downgrade() -> None:
    """**Forward-only. بيعمل حاجة عمليًا — وبيتكلم عن ليه.**

    الأعمدة دي مش كلها بتاعتنا. داتابيز التطوير كان فيها من قبل كده
    (من `c32c9e6af771` وشغل تاني) Sequel:

    - `orders.payment_method_id` + الـFK + الفهرس بتوعه
    - `restaurant_settings.pwa_name` / `pwa_short_name` / `pwa_icon_url`

    الـ`upgrade` تبنّاهم بـ"لو مش موجود" عشان مفيش migration يكسر على
    داتابيز نظيفة ولا على دي. المشكلة إن الـ`downgrade` **مش** يعرف
    مين اللي أضاف إيه: Alembic مالوشسجل للكود اللي اشتغل عليه، والقائمة
    في الداتابيز وقت الـdowngrade هي اللي بعد الـupgrade. فأي
    `drop_column` هيمسح أعمدة كانت موجودة قبل ما الـmigration دي تشتغل
    أصلاً — ودي بيانات، مش schema.

    الاختيار: ما نعملش حاجة. الرد للـparent بيحصل في
    `alembic_version` بس، والداتابيز بتفضل على الحالة المتسقة
    (أعمدة زيادة = آمنة، محتواها زي ما هو). والـ`upgrade` بعده
    idempotent، فالرجوع وبعدين التقديم تاني بيشتغل.

    اللي اتغير فعلاً في الـupgrade ومش راجع:

    - `orders.status` اتطبّع `new` → `pending` (القيمة القديمة ضاعت).
    - `orders.destination` و`restaurant_settings.order_destination`
      اتشالوا بعد ما اتقروا (الـper-order قناة بقت في `orders.channel`،
      والـsite-wide مفيش له معنى في V2).
    - `orders.payment_status` اتعبّى `pending` للصفوف القديمة.

    استرجاع أي من دول محتاج الـbackup dump. وده سبب الـbackup قبل
    الـupgrade من الأصل، مش بعده.
    """
