#!/bin/bash
# agent5 concurrent run(s) on a CPU compute node, models via OpenRouter.
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

# Model creds. OpenRouter by default; a config carrying `provider: azure` (the gpt-5.4
# cells) needs the Azure pair instead — same source as the sj3/sj4 judges, with the repo
# .env as a fallback. proxy.py --upstream azure reads them from the environment.
set -a; source "$PROJECT/.env"; set +a
if grep -lq "^provider: azure" $CONFIGS 2>/dev/null; then
  [ -f /fast/jtaraz/syco-bench/.env ] && { set -a; source /fast/jtaraz/syco-bench/.env; set +a; }
  [ -n "${AZURE_OPENAI_API_KEY:-}" ] || { echo "FATAL: AZURE_OPENAI_API_KEY unset" >&2; exit 1; }
  [ -n "${AZURE_OPENAI_ENDPOINT:-}" ] || { echo "FATAL: AZURE_OPENAI_ENDPOINT unset" >&2; exit 1; }
  # One deployment serving four assistants that all wake at once is the 429 ceiling the
  # judge sweeps hit; proxy.py retries, but do not stack jobs on top of that.
  echo "azure cell: gpt-5.4 via $AZURE_OPENAI_ENDPOINT (no reasoning will be recorded)"
else
  [ -n "${OPENROUTER_API_KEY:-}" ] || { echo "FATAL: OPENROUTER_API_KEY unset" >&2; exit 1; }
fi

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
    "$PROJECT/.venv/bin/python" -m experiments.agent5.runner5 --config "$CONFIG"
    rc=$?
    set -e
    echo "[$(date +%H:%M:%S)] DONE  $CONFIG run $i/$REPEATS rc=$rc"
    # Stray subprocesses must not leak between runs or outlive the job.
    pkill -f "agent5/slack_server.py.*--out $PROJECT" 2>/dev/null || true
    pkill -f "agent4/proxy.py.*--out $PROJECT" 2>/dev/null || true
  done
done
echo "[$(date +%H:%M:%S)] ALL RUNS FINISHED"
