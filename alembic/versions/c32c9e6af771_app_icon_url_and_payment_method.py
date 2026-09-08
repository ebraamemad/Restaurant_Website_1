"""app_icon_url and payment_method

Revision ID: c32c9e6af771
Revises: 4d5e6f7a8b9c
Create Date: 2026-09-08 15:30:55.661856

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c32c9e6af771'
down_revision: Union[str, None] = '4d5e6f7a8b9c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('restaurant_settings', sa.Column('app_icon_url', sa.String(length=500), nullable=True))
    op.add_column('products', sa.Column('is_featured', sa.Boolean(), nullable=True, server_default=sa.false()))
    op.add_column('products', sa.Column('is_offer', sa.Boolean(), nullable=True, server_default=sa.false()))
    op.drop_column('categories', 'is_featured')


def downgrade() -> None:
    op.drop_column('products', 'is_offer')
    op.drop_column('products', 'is_featured')
    op.drop_column('restaurant_settings', 'app_icon_url')