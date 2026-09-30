"""Phase 4: Marketing Center — customers, segments, CSV import, campaigns

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-09-29 18:00:00.000000

## Phase 4 — Marketing

Additive. Two tables extended, three tables created, one constraint
relaxed. No existing row is deleted or modified except the
`customers` backfill below.

### 1. `customers` — extended

- `source` — `order` (default) or `import`. The backfill sets
  `order` for every existing row, which is factually right: every
  customer that existed before this migration got there through an
  order. Segmentation can now distinguish a lead imported from a CSV
  from a customer with real order history, and the reports treat
  them differently.
- `marketing_opt_out` / `marketing_opt_out_at` — explicit marketing
  consent. `False` means "not opted out", not "unknown": the
  cheapest default for a one-way digital channel, and it keeps the
  `NOT NULL` guarantee the segment filters rely on. The transactional
  order-status notifications deliberately do **not** read this column.

### 2. `customers.first_order_at` / `last_order_at` — DROP NOT NULL

These were `NOT NULL` because every customer came from an order. CSV
import creates customers with no orders, so a strict `NOT NULL` would
force one of two bad outcomes: break the import, or store a fake
"first order" timestamp that then shows up in a marketing report as
if the customer had purchased. `NULL` means "has not ordered yet",
which is the truth.

Application code that reads these columns already handles `None`:
`admin/customers.html` guards with `if customer.first_order_at`, and
`app/segments.py` derives every date from the `orders` table rather
than the denormalized columns.

### 3. `contact_imports` / `contact_import_rows` — new

The import is a five-step flow (upload → validate → normalize →
deduplicate → preview → import) and a 10,000-row file cannot survive
a round trip through a hidden form field or a cookie. The batch and
its rows live in the database; the preview reads them by `import_id`.
That is also the audit trail: who imported what, when, and how many
rows were lost and why.

Rows keep the raw cell values next to the normalized ones. Without
the raw copy an admin cannot tell "the phone was wrong in the file"
from "we rewrote it", or "this row repeated" from "this phone is
already on file".

### 4. `campaigns` / `campaign_recipients` — new

`campaigns` is the message header. `campaign_recipients` is the
per-device delivery ledger, and it is the reason the campaign's
`total_recipients` / `sent_count` / `failed_count` are trustworthy:
they are **computed from the ledger**, not predicted before sending.

`ck_campaigns_audience_segment` makes "everyone" plus a segment key
structurally impossible rather than something the application code
has to remember to check.

### Indexes

Each index here serves a query that actually runs in the admin UI:

- `campaigns (status, scheduled_at)` — the lazy scheduler's scan for
  campaigns that came due.
- `campaigns created_at` — campaign history, newest first.
- `campaign_recipients (campaign_id, status)` — the chunked send
  loop fetches `pending` rows in id order; it re-runs per chunk, so
  this index is on the hot path.
- `contact_import_rows (import_id, status)` — the preview filtered
  by "show me only the invalid ones".

### Downgrade

Forward-only, like `d4e5f6a7b8c9` and `e5f6a7b8c9d0`. A marketing
campaign that was actually sent is a business fact, and a customer
who opted out must not silently become opted back in because someone
rolled the schema back. Dropping `marketing_opt_out` would restore
consent the customer explicitly withdrew.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "f6a7b8c9d0e1"
down_revision: str = "e5f6a7b8c9d0"
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


def _alter_nullable(table: str, column: str, nullable: bool) -> None:
    """يغيّر `NOT NULL` على عمود موجود.

    بنعملها idempotent عن طريق قراءة الـnullability الأول: PostgreSQL
    بيقبل `ALTER COLUMN` على عمود في الحالة المطلوبة من غير error،
    بس بنتحقق برضه عشان الـroundtrip test (downgrade → upgrade) يبقى
    نضيف من غير أخطاء متوقعة.
    """
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return
    for col in inspector.get_columns(table):
        if col["name"] == column and col["nullable"] != nullable:
            op.alter_column(table, column, nullable=nullable)


def _create_index_if_missing(
    name: str, table: str, columns: list[str], unique: bool = False
) -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return
    if name in {ix["name"] for ix in inspector.get_indexes(table)}:
        return
    op.create_index(name, table, columns, unique=unique)


def _create_check_if_missing(name: str, table: str, condition: str) -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return
    if any(ck.get("name") == name for ck in inspector.get_check_constraints(table)):
        return
    op.create_check_constraint(name, table, condition)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. customers — source + marketing consent
    # ------------------------------------------------------------------
    _add_column_if_missing(
        "customers",
        sa.Column("source", sa.String(length=20), nullable=False, server_default="order"),
    )
    _add_column_if_missing(
        "customers",
        sa.Column(
            "marketing_opt_out", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    _add_column_if_missing(
        "customers", sa.Column("marketing_opt_out_at", sa.DateTime(timezone=True), nullable=True)
    )
    _create_index_if_missing("ix_customers_source", "customers", ["source"])
    _create_index_if_missing("ix_customers_marketing_opt_out", "customers", ["marketing_opt_out"])
    _create_check_if_missing("ck_customers_source", "customers", "source IN ('order', 'import')")

    # 2. Imported contacts have no orders, so no order dates. See the
    #    module docstring for why a fake timestamp would be worse.
    _alter_nullable("customers", "first_order_at", True)
    _alter_nullable("customers", "last_order_at", True)

    # ------------------------------------------------------------------
    # 2. contact_imports / contact_import_rows — CSV import audit trail
    # ------------------------------------------------------------------
    if not _table_exists("contact_imports"):
        op.create_table(
            "contact_imports",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("filename", sa.String(length=255), nullable=False),
            sa.Column(
                "status", sa.String(length=20), nullable=False, server_default="previewed"
            ),
            sa.Column("total_rows", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("valid_rows", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("invalid_rows", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("duplicate_rows", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("imported_rows", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_by_admin_id", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error", sa.String(length=300), nullable=True),
            sa.ForeignKeyConstraint(
                ["created_by_admin_id"],
                ["admins.id"],
                name="fk_contact_imports_created_by_admin_id",
                ondelete="SET NULL",
            ),
            sa.CheckConstraint(
                "status IN ('previewed', 'imported', 'discarded')",
                name="ck_contact_imports_status",
            ),
        )
    _create_index_if_missing("ix_contact_imports_status", "contact_imports", ["status"])
    _create_index_if_missing(
        "ix_contact_imports_created_by_admin_id", "contact_imports", ["created_by_admin_id"]
    )

    if not _table_exists("contact_import_rows"):
        op.create_table(
            "contact_import_rows",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("import_id", sa.Integer(), nullable=False),
            sa.Column("row_number", sa.Integer(), nullable=False),
            sa.Column("raw_name", sa.String(length=300), nullable=True),
            sa.Column("raw_phone", sa.String(length=120), nullable=True),
            sa.Column("normalized_phone", sa.String(length=30), nullable=True),
            sa.Column("name", sa.String(length=120), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="valid"),
            sa.Column("error", sa.String(length=300), nullable=True),
            sa.Column("existing_customer_id", sa.Integer(), nullable=True),
            sa.Column("customer_id", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            # CASCADE: the rows have no meaning without the batch. They
            # are the preview, and a discarded preview should not leave
            # 10,000 orphaned rows behind.
            sa.ForeignKeyConstraint(
                ["import_id"],
                ["contact_imports.id"],
                name="fk_contact_import_rows_import_id",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["existing_customer_id"],
                ["customers.id"],
                name="fk_contact_import_rows_existing_customer_id",
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["customer_id"],
                ["customers.id"],
                name="fk_contact_import_rows_customer_id",
                ondelete="SET NULL",
            ),
            sa.CheckConstraint(
                "status IN ('valid', 'invalid', 'duplicate', 'imported')",
                name="ck_contact_import_rows_status",
            ),
        )
    _create_index_if_missing("ix_contact_import_rows_import_id", "contact_import_rows", ["import_id"])
    _create_index_if_missing("ix_contact_import_rows_status", "contact_import_rows", ["status"])
    _create_index_if_missing(
        "ix_contact_import_rows_normalized_phone", "contact_import_rows", ["normalized_phone"]
    )
    _create_index_if_missing(
        "ix_contact_import_rows_import_status", "contact_import_rows", ["import_id", "status"]
    )

    # ------------------------------------------------------------------
    # 3. campaigns
    # ------------------------------------------------------------------
    if not _table_exists("campaigns"):
        op.create_table(
            "campaigns",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("title", sa.String(length=160), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("image_url", sa.String(length=600), nullable=True),
            sa.Column("link_url", sa.String(length=600), nullable=True),
            sa.Column(
                "audience_type", sa.String(length=20), nullable=False, server_default="all"
            ),
            sa.Column("segment_key", sa.String(length=40), nullable=True),
            sa.Column("channel", sa.String(length=20), nullable=False, server_default="push"),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
            sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancel_reason", sa.String(length=300), nullable=True),
            sa.Column(
                "total_recipients", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column("sent_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("invalid_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_by_admin_id", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(
                ["created_by_admin_id"],
                ["admins.id"],
                name="fk_campaigns_created_by_admin_id",
                ondelete="SET NULL",
            ),
            sa.CheckConstraint(
                "channel IN ('push', 'whatsapp', 'both')", name="ck_campaigns_channel"
            ),
            sa.CheckConstraint(
                "status IN ('draft', 'scheduled', 'sending', 'completed', "
                "'partially_completed', 'cancelled', 'failed')",
                name="ck_campaigns_status",
            ),
            sa.CheckConstraint(
                "audience_type IN ('all', 'segment', 'imported')",
                name="ck_campaigns_audience_type",
            ),
            # "Everyone" plus a segment key is contradictory data. The
            # constraint rejects it at the storage layer; application
            # code returns a friendlier Arabic message for the same rule.
            sa.CheckConstraint(
                "(audience_type = 'segment' AND segment_key IS NOT NULL) OR "
                "(audience_type <> 'segment' AND segment_key IS NULL)",
                name="ck_campaigns_audience_segment",
            ),
        )
    _create_index_if_missing("ix_campaigns_status", "campaigns", ["status"])
    _create_index_if_missing("ix_campaigns_channel", "campaigns", ["channel"])
    _create_index_if_missing("ix_campaigns_audience_type", "campaigns", ["audience_type"])
    _create_index_if_missing("ix_campaigns_segment_key", "campaigns", ["segment_key"])
    _create_index_if_missing("ix_campaigns_created_at", "campaigns", ["created_at"])
    _create_index_if_missing(
        "ix_campaigns_created_by_admin_id", "campaigns", ["created_by_admin_id"]
    )
    # The lazy scheduler's exact query: status = 'scheduled' AND
    # scheduled_at <= now, ordered by scheduled_at.
    _create_index_if_missing(
        "ix_campaigns_status_scheduled", "campaigns", ["status", "scheduled_at"]
    )

    # ------------------------------------------------------------------
    # 4. campaign_recipients — the per-device ledger
    # ------------------------------------------------------------------
    if not _table_exists("campaign_recipients"):
        op.create_table(
            "campaign_recipients",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("campaign_id", sa.Integer(), nullable=False),
            sa.Column("customer_id", sa.Integer(), nullable=True),
            sa.Column("subscription_id", sa.Integer(), nullable=True),
            sa.Column("customer_name", sa.String(length=120), nullable=True),
            sa.Column("customer_phone", sa.String(length=30), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("error", sa.String(length=300), nullable=True),
            sa.Column("response_code", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            # CASCADE: the ledger is meaningless without its campaign.
            sa.ForeignKeyConstraint(
                ["campaign_id"],
                ["campaigns.id"],
                name="fk_campaign_recipients_campaign_id",
                ondelete="CASCADE",
            ),
            # SET NULL, not CASCADE: the row is evidence that we tried to
            # reach this customer. Deleting the customer should not
            # rewrite the campaign's success rate.
            sa.ForeignKeyConstraint(
                ["customer_id"],
                ["customers.id"],
                name="fk_campaign_recipients_customer_id",
                ondelete="SET NULL",
            ),
            # The subscription can be deleted when a provider reports a
            # dead endpoint. The recipient row survives with
            # `status='invalid'` and the phone snapshot next to it.
            sa.ForeignKeyConstraint(
                ["subscription_id"],
                ["push_subscriptions.id"],
                name="fk_campaign_recipients_subscription_id",
                ondelete="SET NULL",
            ),
            sa.CheckConstraint(
                "status IN ('pending', 'sent', 'failed', 'skipped', 'invalid')",
                name="ck_campaign_recipients_status",
            ),
        )
    _create_index_if_missing(
        "ix_campaign_recipients_campaign_id", "campaign_recipients", ["campaign_id"]
    )
    _create_index_if_missing(
        "ix_campaign_recipients_customer_id", "campaign_recipients", ["customer_id"]
    )
    _create_index_if_missing("ix_campaign_recipients_status", "campaign_recipients", ["status"])
    # The send loop's hot path: fetch `pending` rows in id order, once
    # per chunk.
    _create_index_if_missing(
        "ix_campaign_recipients_campaign_status",
        "campaign_recipients",
        ["campaign_id", "status"],
    )


def downgrade() -> None:
    """**Forward-only, like the two previous revisions.**

    The rationale is stronger here than it was for the push columns.
    A campaign that was sent, a customer's recorded opt-out, and a
    file of imported contacts are all facts about the business. A
    `DROP TABLE` would destroy them, and dropping `marketing_opt_out`
    would silently restore consent that a customer explicitly
    withdrew — the one direction of error that has legal weight.

    This downgrade records the parent revision in `alembic_version`
    only.
    """
