#!/bin/bash
# agent4 concurrent run(s) on a CPU compute node, models via OpenRouter.
#
#   condor_submit_bid 100 cluster/run_agent4_conc.sub \
#       config="experiments/agent4/configs/agent4_v15c_askNone_conc_deepseek.yaml" repeats=1
#
# Repeats run sequentially inside one job (each gets its own timestamped run dir);
# submit separate jobs for parallelism. Run records land in
# $PROJECT/experiments/agent4/runs/ on /fast — pull them back with
#   bash cluster/sync_agent4.sh --runs
export HOME="${HOME:-/home/jtaraz}"
set -euo pipefail
export PYTHONUNBUFFERED=1

PROJECT=/fast/jtaraz/LIARS/colosseum-detection
OPENCODE_DIR=/fast/jtaraz/opencode
cd "$PROJECT"

CONFIGS="${1:?config path(s) required (space-separated)}"
REPEATS="${2:-1}"
CLUSTER_ID="${3:-$$}"

# opencode binary (installed by cluster/setup_agent4.sh)
export PATH="$OPENCODE_DIR/bin:$PATH"
opencode --version >/dev/null || { echo "FATAL: opencode missing — run setup_agent4.sub first" >&2; exit 1; }

# OpenRouter creds only (same rationale as run_agent1_openrouter.sh).
set -a; source "$PROJECT/.env"; set +a
[ -n "${OPENROUTER_API_KEY:-}" ] || { echo "FATAL: OPENROUTER_API_KEY unset" >&2; exit 1; }

# Compute nodes reach the internet only through the HTTP(S) proxy that condor's env
# provides. Everything agent4 runs on localhost (world MCP, logging proxy, opencode
# serves) must BYPASS it, while proxy.py's upstream leg to openrouter.ai uses it.
export NO_PROXY="127.0.0.1,localhost${NO_PROXY:+,$NO_PROXY}"
export no_proxy="$NO_PROXY"

# Two agent4 jobs can share a node: shift every port by a per-job offset derived from the
# condor Cluster id (consecutive submissions → distinct offsets).
export AGENT4_PORT_OFFSET=$(( (CLUSTER_ID % 40) * 25 ))
echo "port offset: $AGENT4_PORT_OFFSET (cluster $CLUSTER_ID)"

for CONFIG in $CONFIGS; do
  for i in $(seq 1 "$REPEATS"); do
    echo "[$(date +%H:%M:%S)] START $CONFIG  run $i/$REPEATS"
    set +e
    "$PROJECT/.venv/bin/python" -m experiments.agent4.runner_conc --config "$CONFIG"
    rc=$?
    set -e
    echo "[$(date +%H:%M:%S)] DONE  $CONFIG run $i/$REPEATS rc=$rc"
    # Stray subprocesses must not leak between runs or outlive the job.
    pkill -f "agent4/world_server.py.*--out $PROJECT" 2>/dev/null || true
    pkill -f "agent4/proxy.py.*--out $PROJECT" 2>/dev/null || true
    # Home trees (~450MB per rollout) pile up in /tmp/tanager and fill the node's small
    # /tmp by rollout ~11. Dirs are named <stamp>-<runner pid>: reap those whose runner
    # is gone (safe on a shared node — other live jobs' runners hold their pids).
    for d in /tmp/tanager/*-*; do
      [ -d "$d" ] || continue
      kill -0 "${d##*-}" 2>/dev/null || rm -rf "$d"
    done
  done
done
echo "[$(date +%H:%M:%S)] ALL RUNS FINISHED"
