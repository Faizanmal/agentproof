"""password reset tokens and mail purpose"""

import sqlalchemy as sa
from alembic import op

revision = "b7c4e2a91d03"  # pragma: allowlist secret -- migration identifier
down_revision = "e542c1a708bf"  # pragma: allowlist secret -- migration identifier
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "password_resets",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_password_resets_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_password_resets")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_password_resets_token_hash")),
    )
    op.create_index(
        op.f("ix_password_resets_expires_at"), "password_resets", ["expires_at"], unique=False
    )
    op.create_index(
        op.f("ix_password_resets_user_id"), "password_resets", ["user_id"], unique=False
    )
    op.add_column(
        "mail_outbox",
        sa.Column("purpose", sa.String(length=32), server_default="verification", nullable=False),
    )
    op.create_check_constraint(
        "ck_mail_outbox_purpose",
        "mail_outbox",
        "purpose IN ('verification','password_reset')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_mail_outbox_purpose", "mail_outbox", type_="check")
    op.drop_column("mail_outbox", "purpose")
    op.drop_index(op.f("ix_password_resets_user_id"), table_name="password_resets")
    op.drop_index(op.f("ix_password_resets_expires_at"), table_name="password_resets")
    op.drop_table("password_resets")
