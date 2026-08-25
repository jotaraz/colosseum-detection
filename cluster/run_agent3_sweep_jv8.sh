#!/bin/bash
# jv8 lie sweep over the v15 agent1 ask arms — the warm start for a reward_v3 run.
# COMPUTE node only: a few hundred OpenRouter calls, not login-node work.
#
#   condor_submit_bid 100 cluster/run_agent3_sweep_jv8.sub
#
# $1 arms (comma separated)   $2 workers   $3 limit (0 = all)
set -uo pipefail
export HOME="${HOME:-/home/jtaraz}"
export PYTHONUNBUFFERED=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT" || exit 1
export PYTHONPATH="$PROJECT"
set -a; source "$PROJECT/.env"; set +a
export OPENROUTER_MAX_RETRIES="${OPENROUTER_MAX_RETRIES:-2}"

ARMS="${1:-askA,askG,askK,askL,askM,askP1,askP3,askP4,askP5}"
WORKERS="${2:-8}"
LIMIT="${3:-0}"

echo "[$(date +%H:%M:%S)] host=$(hostname) proxy=${https_proxy:-<none>}"
echo "[$(date +%H:%M:%S)] arms=$ARMS workers=$WORKERS limit=$LIMIT"
[ -n "${OPENROUTER_API_KEY:-}" ] || { echo "FATAL: OPENROUTER_API_KEY unset" >&2; exit 1; }
"$PROJECT/.venv/bin/python" -m experiments.agent3.smoke || { echo "FATAL: smoke failed" >&2; exit 1; }

"$PROJECT/.venv/bin/python" -m experiments.agent3.sweep_jv8 \
    --arms "$ARMS" --workers "$WORKERS" --limit "$LIMIT" \
    2>&1 | tee -a "$PROJECT/logs/agent3_sweep_jv8.log"
rc=${PIPESTATUS[0]}
echo "[$(date +%H:%M:%S)] sweep exited rc=$rc"
exit "$rc"
