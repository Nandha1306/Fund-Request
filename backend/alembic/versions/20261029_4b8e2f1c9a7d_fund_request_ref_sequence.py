"""fund request reference sequence

Revision ID: 4b8e2f1c9a7d
Revises: d576ccc6356f
Create Date: 2026-10-29 18:00:00.000000

``ref_no = FR-{YYYY}-{n:04d}``: ``n`` comes from this sequence, so two concurrent submits
never collide and no lock is needed (gaps after a rolled-back submit are fine).
"""
from collections.abc import Sequence

from alembic import op

revision: str = '4b8e2f1c9a7d'
down_revision: str | None = 'd576ccc6356f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE fund_request_ref_seq START WITH 1 INCREMENT BY 1")


def downgrade() -> None:
    op.execute("DROP SEQUENCE fund_request_ref_seq")
