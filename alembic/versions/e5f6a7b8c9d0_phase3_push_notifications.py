"""Phase 3: Web Push delivery, subscriptions, order status history

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-29 12:00:00.000000

## Phase 3 — Notifications

Additive only. Every change either creates a new table, adds a nullable
column, or inserts a backfill row. Nothing is dropped and no existing row
is modified.

### 1. `push_subscriptions` — extended

The table and its unique index on `endpoint` already existed from
`d4e5f6a7b8c9`, declared but unused. Phase 3 gives it the columns that
turn it from a placeholder into a working subscription store:

- `admin_id` — who the subscription belongs to. A subscription is either
  a customer's or an admin's, never both: exactly one of the two FKs is
  non-null. `ON DELETE CASCADE` for `admin_id` because deleting an admin
  should take their devices with it; `SET NULL` for `customer_id` because
  customer history is not ours to delete.
- `status` / `invalid_at` / `failure_count` / `last_success_at` — the
  push provider answers `404`/`410` when an endpoint is gone. That is
  not a bug in our code, it is information: we must stop sending to that
  device. `status='invalid'` records that without deleting the row,
  because the row owns an audit trail (`push_notifications` cascades off
  it) and deleting it would erase the history too.

### 2. `order_status_history` — new

`orders.status` is the current state only. Without a history table the
tracking page can say "now preparing" but not when each state was
reached. This is the extension point `PHASE1_REPORT.md` and
`app/order_tracking.py` both named explicitly.

The `synthetic` flag marks the backfilled opening rows. We truthfully
know those orders existed in a given state; we do **not** know when
they changed, so the timestamp is the order's own `created_at`. Rows
written by the application have `synthetic=false` and a real
`changed_at`.

### 3. `push_notifications` — new

Send history: who was told what, and with what result. This is what
lets a customer see recent notifications inside the site when the
service worker is not running, and lets an admin confirm whether a given
order was actually announced. It is also the only way to tell "the
channel is broken" apart from "the user was not looking".

The signed payload itself is not stored — it is momentary data and the
row outlives it. `title` / `body` / `url` are stored as columns instead.

### 4. Indexes

Three composite indexes exist to serve the three queries that actually
run in production:

- `(customer_id, status)` — every customer status notification.
- `(admin_id, status)` — the new-order fan-out.
- `(order_id, order_id)`-equivalent coverage via `ix_push_notifications_order_id`
  for the per-order support view.

`order_status_history` gets `(order_id, changed_at)` so reading one
order's timeline is an index scan, not a sort.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "e5f6a7b8c9d0"
down_revision: str = "d4e5f6a7b8c9"
branch_labels: str | None = None
depends_on: str | None = None


def _table_exists(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return False
    return any(c["name"] == column for c in inspector.get_columns(table))


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    if not _has_column(table, column.name):
        op.add_column(table, column)


def _create_index_if_missing(name: str, table: str, columns: list[str], unique: bool = False) -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return
    existing = {ix["name"] for ix in inspector.get_indexes(table)}
    if name in existing:
        return
    op.create_index(name, table, columns, unique=unique)


def _create_fk_if_missing(
    name: str,
    table: str,
    referent: str,
    local_cols: list[str],
    remote_cols: list[str],
    ondelete: str,
) -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return
    for fk in inspector.get_foreign_keys(table):
        if fk.get("name") == name:
            return
    op.create_foreign_key(name, table, referent, local_cols, remote_cols, ondelete=ondelete)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. push_subscriptions — admin ownership + liveness columns
    # ------------------------------------------------------------------
    # `push_subscriptions` is created by `d4e5f6a7b8c9`, so this revision
    # only ever adds columns to it. Recreating the table here would be a
    # second `create_table` for the same name in the chain.
    _add_column_if_missing(
        "push_subscriptions",
        sa.Column("admin_id", sa.Integer(), nullable=True),
    )
    _add_column_if_missing(
        "push_subscriptions",
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="active",
        ),
    )
    _add_column_if_missing(
        "push_subscriptions", sa.Column("invalid_at", sa.DateTime(timezone=True), nullable=True)
    )
    _add_column_if_missing(
        "push_subscriptions",
        sa.Column(
            "failure_count", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    _add_column_if_missing(
        "push_subscriptions", sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True)
    )

    _create_index_if_missing(
        "ix_push_subscriptions_admin_id", "push_subscriptions", ["admin_id"]
    )
    _create_index_if_missing(
        "ix_push_subscriptions_status", "push_subscriptions", ["status"]
    )
    # The status filter is always combined with the owner filter.
    _create_index_if_missing(
        "ix_push_subscriptions_customer_status",
        "push_subscriptions",
        ["customer_id", "status"],
    )
    _create_index_if_missing(
        "ix_push_subscriptions_admin_status",
        "push_subscriptions",
        ["admin_id", "status"],
    )
    _create_fk_if_missing(
        "fk_push_subscriptions_admin_id",
        "push_subscriptions",
        "admins",
        ["admin_id"],
        ["id"],
        "CASCADE",
    )

    # ------------------------------------------------------------------
    # 2. order_status_history — new
    # ------------------------------------------------------------------
    if not _table_exists("order_status_history"):
        op.create_table(
            "order_status_history",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("order_id", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column(
                "changed_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("changed_by_admin_id", sa.Integer(), nullable=True),
            sa.Column("changed_by_username", sa.String(length=80), nullable=True),
            sa.Column("note", sa.String(length=300), nullable=True),
            sa.Column(
                "synthetic", sa.Boolean(), nullable=False, server_default=sa.text("false")
            ),
        )
    _create_index_if_missing(
        "ix_order_status_history_order_id", "order_status_history", ["order_id"]
    )
    _create_index_if_missing("ix_order_status_history_status", "order_status_history", ["status"])
    _create_index_if_missing(
        "ix_order_status_history_changed_at", "order_status_history", ["changed_at"]
    )
    # Reading one order's timeline in time order: index scan, not a sort.
    _create_index_if_missing(
        "ix_order_status_history_order_changed",
        "order_status_history",
        ["order_id", "changed_at"],
    )
    _create_index_if_missing(
        "ix_order_status_history_admin_id", "order_status_history", ["changed_by_admin_id"]
    )
    _create_fk_if_missing(
        "fk_order_status_history_order_id",
        "order_status_history",
        "orders",
        ["order_id"],
        ["id"],
        "CASCADE",
    )
    # SET NULL: the record of *what happened* outlives the staff account that
    # did it. `changed_by_username` keeps the name readable after deletion.
    _create_fk_if_missing(
        "fk_order_status_history_admin_id",
        "order_status_history",
        "admins",
        ["changed_by_admin_id"],
        ["id"],
        "SET NULL",
    )

    # Backfill: one opening row per existing order, marked synthetic because
    # the transition time is not knowable — only the order's creation time is.
    op.execute(
        """
        INSERT INTO order_status_history
            (order_id, status, changed_at, changed_by_admin_id,
             changed_by_username, note, synthetic)
        SELECT o.id,
               o.status,
               o.created_at,
               NULL,
               NULL,
               NULL,
               true
          FROM orders o
         WHERE NOT EXISTS (
               SELECT 1
                 FROM order_status_history h
                WHERE h.order_id = o.id
             )
        """
    )

    # ------------------------------------------------------------------
    # 3. push_notifications — new
    # ------------------------------------------------------------------
    if not _table_exists("push_notifications"):
        op.create_table(
            "push_notifications",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("subscription_id", sa.Integer(), nullable=True),
            sa.Column("order_id", sa.Integer(), nullable=True),
            sa.Column("audience", sa.String(length=20), nullable=False),
            sa.Column("title", sa.String(length=160), nullable=False),
            sa.Column("body", sa.String(length=300), nullable=False),
            sa.Column("url", sa.String(length=300), nullable=True),
            sa.Column(
                "status", sa.String(length=20), nullable=False, server_default="sent"
            ),
            sa.Column("response_code", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
    _create_index_if_missing(
        "ix_push_notifications_subscription_id", "push_notifications", ["subscription_id"]
    )
    _create_index_if_missing("ix_push_notifications_order_id", "push_notifications", ["order_id"])
    _create_index_if_missing("ix_push_notifications_audience", "push_notifications", ["audience"])
    _create_index_if_missing("ix_push_notifications_status", "push_notifications", ["status"])
    _create_index_if_missing(
        "ix_push_notifications_created_at", "push_notifications", ["created_at"]
    )
    _create_fk_if_missing(
        "fk_push_notifications_subscription_id",
        "push_notifications",
        "push_subscriptions",
        ["subscription_id"],
        ["id"],
        "CASCADE",
    )
    # CASCADE: a notification is a fact about an order. If the order goes,
    # so does the note that it was announced.
    _create_fk_if_missing(
        "fk_push_notifications_order_id",
        "push_notifications",
        "orders",
        ["order_id"],
        ["id"],
        "CASCADE",
    )


def downgrade() -> None:
    """**Forward-only, like `d4e5f6a7b8c9`.**

    The new tables and the backfilled history rows are not removed. The
    `downgrade` records the parent revision in `alembic_version` only.

    Rationale: the backfill invented `created_at`-shaped timestamps marked
    `synthetic=true`. Dropping and re-adding them later would be
    indistinguishable from real data, and Phase 3 has no production
    history to protect yet. Additive leftovers are the safe direction —
    the same argument `d4e5f6a7b8c9` makes for its columns.
    """
