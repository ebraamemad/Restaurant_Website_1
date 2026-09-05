"""normalize customer phone numbers to consistent format

Revision ID: 4d5e6f7a8b9c
Revises: 3a2b1c0d9e8f
Create Date: 2026-09-03

Normalize Customer.phone to the new format (Egyptian numbers: strip leading 0).
This ensures consistency between Customer.phone storage and the first-order
coupon SQL lookup (which also strips the leading 0).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4d5e6f7a8b9c"
down_revision: Union[str, None] = "3a2b1c0d9e8f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        UPDATE customers
        SET phone = regexp_replace(
                       regexp_replace(
                           regexp_replace(phone, '\\D', '', 'g'),
                           '^00', ''),
                       '^0', '')
        WHERE phone ~ '[0-9]'
          AND length(regexp_replace(
                        regexp_replace(
                            regexp_replace(phone, '\\D', '', 'g'),
                            '^00', ''),
                        '^0', '')) < length(phone)
    """))


def downgrade() -> None:
    pass
