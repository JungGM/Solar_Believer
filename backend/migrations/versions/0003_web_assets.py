"""Store frontend files in the service PostgreSQL database."""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "web_assets",
        sa.Column("path", sa.String(512), primary_key=True),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
    )


def downgrade():
    op.drop_table("web_assets")
