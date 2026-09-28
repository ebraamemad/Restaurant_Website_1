"""admin_login_attempts for brute-force protection

Revision ID: c1d2e3f4a5b6
Revises: a1b2c3d4e5f7
Create Date: 2026-09-27 12:00:00.000000

Phase 0 security fix: the admin login form had no attempt limiting, so the
password could be brute-forced at unlimited rate. This table records failed
attempts so `app.login_guard` can throttle per (IP + username) and per IP.

Only SHA-256 HMAC digests are stored — never the raw IP or username — so the
table reveals nothing about who attempted a login.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: str = "a1b2c3d4e5f7"
branch_labels: list[str] | None = None
depends_on: list[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_login_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ip_hash", sa.String(length=64), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_admin_login_attempts_ip_hash", "admin_login_attempts", ["ip_hash"], unique=False
    )
    op.create_index(
        "ix_admin_login_attempts_key_hash", "admin_login_attempts", ["key_hash"], unique=False
    )
    op.create_index(
        "ix_admin_login_attempts_attempted_at",
        "admin_login_attempts",
        ["attempted_at"],
        unique=False,
    )
    # The two counters the guard issues on every check are
    # (key_hash, attempted_at) and (ip_hash, attempted_at). A composite index
    # on (key_hash, attempted_at) keeps the hot lookup index-only.
    op.create_index(
        "ix_admin_login_attempts_key_time",
        "admin_login_attempts",
        ["key_hash", "attempted_at"],
        unique=False,
    )
    op.create_index(
        "ix_admin_login_attempts_ip_time",
        "admin_login_attempts",
        ["ip_hash", "attempted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_admin_login_attempts_ip_time", table_name="admin_login_attempts")
    op.drop_index("ix_admin_login_attempts_key_time", table_name="admin_login_attempts")
    op.drop_index("ix_admin_login_attempts_attempted_at", table_name="admin_login_attempts")
    op.drop_index("ix_admin_login_attempts_key_hash", table_name="admin_login_attempts")
    op.drop_index("ix_admin_login_attempts_ip_hash", table_name="admin_login_attempts")
    op.drop_table("admin_login_attempts")
