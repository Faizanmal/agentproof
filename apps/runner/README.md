# Runner scaffold

`uv run --frozen --all-packages agentproof-runner --check` validates packaging.
Without `--check` the entry point refuses execution. No web API or browser launch.
The future minimal Playwright executor must run only in disposable sandboxes
controlled by the private broker. Never pass API/database/inference settings here.
