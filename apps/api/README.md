# API

FastAPI app factory: `agentproof_api.app:create_app`. Start from the repository
root with `python scripts/manage.py dev`. Alembic configuration and revisions live
here. `/api/v1/health/ready` probes real data services; `/live` is process-only.
