"""Executions, steps, resumable logs and hashed webhook credentials."""

import sqlalchemy as sa
from alembic import op

revision = "001_executions"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "executions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("workflow_id", sa.String(36), nullable=True),
        sa.Column("workflow_version", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("trigger_payload", sa.JSON(), nullable=False),
        sa.Column("event_key", sa.String(64), unique=True, nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        *[
            sa.Column(name, sa.DateTime(timezone=True), nullable=name != "created_at")
            for name in (
                "created_at",
                "started_at",
                "finished_at",
                "heartbeat_at",
                "dispatched_at",
            )
        ],
    )
    for name in ("user_id", "workflow_id", "status"):
        op.create_index(f"ix_executions_{name}", "executions", [name])
    op.create_table(
        "execution_steps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "execution_id",
            sa.String(36),
            sa.ForeignKey("executions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_id", sa.String(64), nullable=False),
        sa.Column("agent_id", sa.String(36), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("execution_id", "node_id", name="uq_execution_node"),
    )
    op.create_index(
        "ix_execution_steps_execution_id", "execution_steps", ["execution_id"]
    )
    op.create_table(
        "execution_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "execution_id",
            sa.String(36),
            sa.ForeignKey("executions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_execution_events_execution_id", "execution_events", ["execution_id"]
    )
    op.create_table(
        "webhook_keys",
        sa.Column("workflow_id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_webhook_keys_user_id", "webhook_keys", ["user_id"])


def downgrade():
    for table in ("webhook_keys", "execution_events", "execution_steps", "executions"):
        op.drop_table(table)
