#!/usr/bin/env sh
# Use the same bounded gateway as the application; never echo tokens or response bodies.
set -eu
exec uv run --frozen --all-packages python scripts/check_vultr_inference.py "$@"
