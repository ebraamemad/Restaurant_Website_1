"""Phase 5: WhatsApp sending — queue columns, attempt log, settings row

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-09-30 12:00:00.000000

## Phase 5 — WhatsApp

Additive. One table extended, two tables created. No existing row is
deleted. Nothing in this revision sends a message: it only creates the
shape that `app/whatsapp/service.py` will fill.

### 1. `campaign_recipients` — extended

**`channel`** — this is the load-bearing change.

Phase 4 stored one row per (campaign, subscription), because only
`push` existed. WhatsApp needs the opposite grain: one row per
(customer, phone), because a customer may have no subscription at all,
or three.

Making the row **(campaign, customer, channel)** instead of
(campaign, subscription) is what lets a `both` campaign stay honest
without a parallel schema. `campaign_id + channel` identifies the unit
of work; `subscription_id` is nullable detail that only `push` rows
populate.

Backfill: every existing row becomes `channel='push'`. That is not an
assumption, it is a fact — before this revision `channel` on a
recipient could only mean push, because the push campaign runner was
the only thing that wrote rows here.

**`attempts` / `next_attempt_at`** — the retry queue.

There is no separate `jobs` table. A pending row with a
`next_attempt_at` in the future *is* the queued item. Two tables for
one piece of state is how a queue and its ledger drift apart, and then
`send_campaign` reports "queued" for work that never runs.

`next_attempt_at IS NULL` means "eligible now", which keeps the first
attempt free of a scheduling round.

**`provider` / `provider_message_id` / `provider_status`** — denormalized
on purpose. The campaign report needs "what did the provider say about
this message" without a third join, and the provider name has to be
pinned at send time: swapping providers later must not rewrite what the
report says happened.

### 2. `whatsapp_attempts` — new

One row per provider call, successful or not.

`campaign_recipients` holds the *last* result, which cannot answer "why
did this take three tries". This table can. Rows are deleted by
`ON DELETE CASCADE` from the recipient, so removing a campaign removes
its delivery trail along with it.

`raw` is `String(600)` and always redacted: a provider that answers
with a full HTML error page must not be able to bloat the database.

### 3. `whatsapp_config` — new, single row

Admin-editable, **non-secret** settings: pacing, batch size, retries,
and the provider URL / instance name. Everything here is safe to show
in the UI and safe to put in a backup.

**There is deliberately no `api_key` column.** The key lives in
`EVOLUTION_API_KEY` in the environment. A column here would put it in
every `admin` `SELECT *` and every backup, and "we masked it in the
form" would be one forgotten template away from a leak.

`enabled` is also not enough on its own — `app/whatsapp/settings.py`
requires `WHATSAPP_ENABLED` in the environment as well. Two switches,
both off by default, so a half-configured deployment cannot send.

## Downgrade: forward-only

Same reason as the previous two revisions, and stronger here. A
delivered message is a fact about the business, and a recorded opt-out
or a bounce is exactly the kind of evidence that should not be
destroyable by a rollback. This downgrade records the parent revision
in `alembic_version` only.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "b8c9d0e1f2a3"
down_revision: str = "a7b8c9d0e1f2"
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


def _index_names(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return set()
    return {ix["name"] for ix in inspector.get_indexes(table)}


def _create_index_if_missing(
    name: str,
    table: str,
    columns: list[str],
    unique: bool = False,
    postgresql_where: sa.TextClause | None = None,
) -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return
    if name in {ix["name"] for ix in inspector.get_indexes(table)}:
        return
    op.create_index(
        name,
        table,
        columns,
        unique=unique,
        postgresql_where=postgresql_where,
    )


def _check_constraint_names(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return set()
    return {
        ck["name"]
        for ck in inspector.get_check_constraints(table)
        if ck.get("name")
    }


def _create_check_if_missing(name: str, table: str, condition: str) -> None:
    if name in _check_constraint_names(table):
        return
    op.create_check_constraint(name, table, condition)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. campaign_recipients — channel + retry queue
    # ------------------------------------------------------------------
    #
    # `server_default='push'` does double duty: it makes the column
    # NOT NULL-safe for the rows that already exist (Postgres fills
    # them during the ALTER), and it keeps the insert path in
    # `send_campaign` honest about stating the channel explicitly.
    _add_column_if_missing(
        "campaign_recipients",
        sa.Column(
            "channel",
            sa.String(length=20),
            nullable=False,
            server_default="push",
        ),
    )
    _add_column_if_missing(
        "campaign_recipients",
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    _add_column_if_missing(
        "campaign_recipients",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    _add_column_if_missing(
        "campaign_recipients",
        sa.Column("provider", sa.String(length=30), nullable=True),
    )
    _add_column_if_missing(
        "campaign_recipients",
        sa.Column("provider_message_id", sa.String(length=120), nullable=True),
    )
    _add_column_if_missing(
        "campaign_recipients",
        sa.Column("provider_status", sa.String(length=60), nullable=True),
    )

    _create_index_if_missing(
        "ix_campaign_recipients_channel", "campaign_recipients", ["channel"]
    )
    _create_index_if_missing(
        "ix_campaign_recipients_next_attempt_at",
        "campaign_recipients",
        ["next_attempt_at"],
    )
    _create_check_if_missing(
        "ck_campaign_recipients_channel",
        "campaign_recipients",
        "channel IN ('push', 'whatsapp')",
    )

    # The worker's only query: due WhatsApp rows for one campaign,
    # oldest first. A plain index on (channel, status, next_attempt_at)
    # would still be used, but the partial form keeps the index small
    # and lets Postgres skip the non-sending rows entirely — and the
    # sending rows are the ones this index is actually about.
    _create_index_if_missing(
        "ix_campaign_recipients_queue",
        "campaign_recipients",
        ["channel", "status", "next_attempt_at"],
        postgresql_where=sa.text(
            "channel = 'whatsapp' AND status = 'pending'"
        ),
    )

    # ------------------------------------------------------------------
    # 2. whatsapp_attempts — one row per provider call
    # ------------------------------------------------------------------
    if not _table_exists("whatsapp_attempts"):
        op.create_table(
            "whatsapp_attempts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "recipient_id",
                sa.Integer(),
                sa.ForeignKey("campaign_recipients.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "campaign_id",
                sa.Integer(),
                sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("attempt_number", sa.Integer(), nullable=False),
            sa.Column("provider", sa.String(length=30), nullable=False),
            sa.Column("state", sa.String(length=20), nullable=False),
            sa.Column("provider_status", sa.String(length=60), nullable=True),
            sa.Column("provider_message_id", sa.String(length=120), nullable=True),
            sa.Column("http_status", sa.Integer(), nullable=True),
            # Short, display-safe. Redacted before it gets here.
            sa.Column("error", sa.String(length=300), nullable=True),
            # Provider body, redacted and truncated. Bounded on purpose:
            # a provider returning an HTML error page must not be able to
            # grow this table without limit.
            sa.Column("raw", sa.String(length=600), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.CheckConstraint(
                "state IN ('sent', 'failed', 'skipped')",
                name="ck_whatsapp_attempts_state",
            ),
        )
        _create_index_if_missing(
            "ix_whatsapp_attempts_recipient_id", "whatsapp_attempts", ["recipient_id"]
        )
        _create_index_if_missing(
            "ix_whatsapp_attempts_campaign_id", "whatsapp_attempts", ["campaign_id"]
        )
        _create_index_if_missing(
            "ix_whatsapp_attempts_recipient",
            "whatsapp_attempts",
            ["recipient_id", "attempt_number"],
        )

    # ------------------------------------------------------------------
    # 3. whatsapp_config — one non-secret settings row
    # ------------------------------------------------------------------
    #
    # No `api_key` column, on purpose. See the module docstring.
    if not _table_exists("whatsapp_config"):
        op.create_table(
            "whatsapp_config",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "sending_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column("base_url", sa.String(length=300), nullable=True),
            sa.Column("instance", sa.String(length=120), nullable=True),
            sa.Column(
                "message_interval_ms",
                sa.Integer(),
                nullable=False,
                server_default="1000",
            ),
            sa.Column(
                "batch_size", sa.Integer(), nullable=False, server_default="20"
            ),
            sa.Column(
                "max_retries", sa.Integer(), nullable=False, server_default="2"
            ),
            sa.Column(
                "updated_by_admin_id",
                sa.Integer(),
                sa.ForeignKey("admins.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        _create_index_if_missing(
            "ix_whatsapp_config_updated_by_admin_id",
            "whatsapp_config",
            ["updated_by_admin_id"],
        )

    # Seed the single row. Both switches start closed: nothing can send
    # until an admin opens the DB switch *and* the deployment sets
    # WHATSAPP_ENABLED in the environment.
    bind = op.get_bind()
    whatsapp_config = sa.table(
        "whatsapp_config",
        sa.column("id", sa.Integer),
        sa.column("enabled", sa.Boolean),
        sa.column("sending_enabled", sa.Boolean),
    )
    existing = bind.execute(
        sa.select(whatsapp_config.c.id).select_from(whatsapp_config).limit(1)
    ).first()
    if existing is None:
        bind.execute(
            whatsapp_config.insert().values(
                id=1, enabled=False, sending_enabled=False
            )
        )


def downgrade() -> None:
    """**Forward-only, like the previous revisions.**

    A delivered campaign, a bounce, and an attempt log are facts about
    the business. Dropping them would destroy the only record of what
    was sent to which customer. This downgrade records the parent
    revision in `alembic_version` only.
    """