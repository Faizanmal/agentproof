"""One test-login label per project."""

from alembic import op

revision = "c4d8e1a09b22"  # pragma: allowlist secret -- Alembic revision identifier
down_revision = "6a2329149ea4"  # pragma: allowlist secret -- Alembic revision identifier
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        op.f("uq_secret_refs_project_id"), "secret_refs", ["project_id", "label"]
    )


def downgrade() -> None:
    op.drop_constraint(op.f("uq_secret_refs_project_id"), "secret_refs", type_="unique")
