"""chunk kind, anchor and paper html_url

Revision ID: 92f470076503
Revises: e73f5e08851d
Create Date: 2026-11-21 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '92f470076503'
down_revision: Union[str, Sequence[str], None] = 'e73f5e08851d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 块的类型：正文、图、表。已有的行都是正文，所以默认值是 'text'
    op.execute("""
        ALTER TABLE chunks ADD COLUMN kind TEXT NOT NULL DEFAULT 'text'
            CHECK (kind IN ('text', 'figure', 'table'))
    """)
    # HTML 里的位置 id，如 S3.SS2.SSS2、S3.F3；用 PDF 解析的块为空
    op.execute("ALTER TABLE chunks ADD COLUMN anchor TEXT")
    # 论文 HTML 版本的地址；为空表示这篇论文是用 PDF 解析的
    op.execute("ALTER TABLE papers ADD COLUMN html_url TEXT")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE papers DROP COLUMN html_url")
    op.execute("ALTER TABLE chunks DROP COLUMN anchor")
    op.execute("ALTER TABLE chunks DROP COLUMN kind")
