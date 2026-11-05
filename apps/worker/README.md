# Worker scaffold

`uv run --frozen --all-packages agentproof-worker --check` validates packaging.
Without `--check` the entry point refuses execution. Scheduler, durable queue,
leases and cancellation are later work. No runtime socket or browser capability.
