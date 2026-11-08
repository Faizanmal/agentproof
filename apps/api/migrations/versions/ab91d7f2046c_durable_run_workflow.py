"""durable run leases and transactional event outbox"""

import sqlalchemy as sa
from alembic import op

revision = "ab91d7f2046c"  # pragma: allowlist secret -- migration identifier
down_revision = "a30355f74cdf"  # pragma: allowlist secret -- migration identifier
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("worker_lease_id", sa.Uuid(), nullable=True))
    op.add_column(
        "runs", sa.Column("worker_lease_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "runs",
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "runs", sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "ix_runs_worker_lease_expires_at", "runs", ["worker_lease_expires_at"], unique=False
    )
    op.create_check_constraint(
        op.f("ck_runs_attempt_count_nonnegative"), "runs", "attempt_count >= 0"
    )

    op.add_column("run_events", sa.Column("published_at", sa.DateTime(timezone=True)))
    op.add_column(
        "run_events",
        sa.Column("publish_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.execute("UPDATE run_events SET published_at=created_at")
    op.create_index(
        "ix_run_events_pending_outbox",
        "run_events",
        ["id"],
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.create_check_constraint(
        op.f("ck_run_events_publish_attempts_nonnegative"),
        "run_events",
        "publish_attempts >= 0",
    )

    op.add_column("sandbox_leases", sa.Column("broker_job_id", sa.Uuid(), nullable=True))
    op.add_column("sandbox_leases", sa.Column("broker_nonce", sa.String(128), nullable=True))
    op.add_column(
        "sandbox_leases",
        sa.Column("last_broker_event_id", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_unique_constraint(
        op.f("uq_sandbox_leases_broker_job_id"), "sandbox_leases", ["broker_job_id"]
    )
    _install_transition_trigger(allow_queue_after_approval=True)


def downgrade() -> None:
    _install_transition_trigger(allow_queue_after_approval=False)
    op.drop_constraint(op.f("uq_sandbox_leases_broker_job_id"), "sandbox_leases", type_="unique")
    op.drop_column("sandbox_leases", "last_broker_event_id")
    op.drop_column("sandbox_leases", "broker_nonce")
    op.drop_column("sandbox_leases", "broker_job_id")
    op.drop_constraint(
        op.f("ck_run_events_publish_attempts_nonnegative"), "run_events", type_="check"
    )
    op.drop_index("ix_run_events_pending_outbox", table_name="run_events")
    op.drop_column("run_events", "publish_attempts")
    op.drop_column("run_events", "published_at")
    op.drop_constraint(op.f("ck_runs_attempt_count_nonnegative"), "runs", type_="check")
    op.drop_index("ix_runs_worker_lease_expires_at", table_name="runs")
    op.drop_column("runs", "cancel_requested_at")
    op.drop_column("runs", "attempt_count")
    op.drop_column("runs", "worker_lease_expires_at")
    op.drop_column("runs", "worker_lease_id")


def _install_transition_trigger(*, allow_queue_after_approval: bool) -> None:
    op.execute("DROP TRIGGER a_run_state ON runs")
    op.execute("DROP FUNCTION ap_run_state()")
    approved_edge = (
        "(OLD.status='awaiting_approval' AND NEW.status IN ('queued','running'))"
        if allow_queue_after_approval
        else "(OLD.status='awaiting_approval' AND NEW.status='running')"
    )
    op.execute(
        "CREATE FUNCTION ap_run_state() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "BEGIN IF OLD.status IN ('completed','failed','cancelled','timed_out') THEN "
        "RAISE EXCEPTION 'Terminal run is immutable'; END IF; IF NEW.status != OLD.status AND "
        "NOT ((OLD.status='queued' AND NEW.status='planning') OR "
        "(OLD.status='planning' AND NEW.status='awaiting_approval') OR "
        + approved_edge
        + " OR (OLD.status='running' AND NEW.status='investigating') OR "
        "(OLD.status='investigating' AND NEW.status='completed') OR "
        "NEW.status IN ('failed','cancelled','timed_out')) THEN "
        "RAISE EXCEPTION 'Invalid run transition'; END IF; RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER a_run_state BEFORE UPDATE ON runs FOR EACH ROW "
        "EXECUTE FUNCTION ap_run_state()"
    )
