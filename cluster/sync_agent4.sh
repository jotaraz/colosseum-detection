#!/bin/bash
# Deploy the local working tree to the cluster (rsync, NOT git pull — cluster convention).
# Run LOCALLY. Excludes bulky/derived state; .env IS synced (the jobs source it for
# OPENROUTER_API_KEY).
#
#   bash cluster/sync_agent4.sh            # push code
#   bash cluster/sync_agent4.sh --runs     # additionally pull agent4 run records back
set -euo pipefail

LOCAL="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE="jtaraz@login.cluster.is.localnet:/fast/jtaraz/LIARS/colosseum-detection"

# No --delete-excluded: macOS's openrsync errors on it (status 22).
rsync -a \
  --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
  --exclude 'experiments/agent4/runs' \
  --exclude 'experiments/agent5/runs' \
  --exclude 'outputs' \
  "$LOCAL/" "$REMOTE/"
echo "SYNC-OK"

if [ "${1:-}" = "--runs" ]; then
  rsync -av "$REMOTE/experiments/agent4/runs/" "$LOCAL/experiments/agent4/runs/"
fi
if [ "${1:-}" = "--runs5" ]; then
  rsync -av "$REMOTE/experiments/agent5/runs/" "$LOCAL/experiments/agent5/runs/"
fi
