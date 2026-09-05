"""restaurant settings description

Revision ID: d8e3c5b72a19
Revises: 9b7e4c2d15a8
Create Date: 2026-08-27

- restaurant_settings.description: وصف المطعم (يظهر في الموقع)، منفصل عن additional_info الخاص بالـFooter.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d8e3c5b72a19"
down_revision: Union[str, None] = "c8f1a2b3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("restaurant_settings", sa.Column("description", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("restaurant_settings", "description")
