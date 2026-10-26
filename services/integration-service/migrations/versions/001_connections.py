"""Encrypted connections, demo artifacts and polling cursors."""

import sqlalchemy as sa
from alembic import op

revision = "001_connections"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("encrypted_credentials", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("is_mock", sa.Boolean(), nullable=False),
        sa.Column("poll_config", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_connections_user_id", "connections", ["user_id"])
    op.create_index("ix_connections_status", "connections", ["status"])
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "connection_id",
            sa.String(36),
            sa.ForeignKey("connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_artifacts_connection_id", "artifacts", ["connection_id"])
    op.create_index("ix_artifacts_kind", "artifacts", ["kind"])
    op.create_table(
        "poll_cursors",
        sa.Column(
            "connection_id",
            sa.String(36),
            sa.ForeignKey("connections.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("state", sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("poll_cursors")
    op.drop_table("artifacts")
    op.drop_table("connections")
