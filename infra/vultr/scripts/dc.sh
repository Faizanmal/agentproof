#!/usr/bin/env bash
# Deprecated name: Ubuntu /usr/bin/dc is GNU desk calculator (hangs on stdin).
# Use apc instead. This stub forwards for one release.
echo "agentproof: use 'apc' not 'dc' (dc is the system desk calculator)" >&2
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/apc.sh" "$@"
