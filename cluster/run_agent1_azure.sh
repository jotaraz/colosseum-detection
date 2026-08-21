#!/bin/bash
# agent1 rollouts with an Azure OpenAI deployment as the AGENT model (not the judge).
#
# CPU-only: the whole job is API wall-clock, four assistants taking turns against Azure. No
# GPU, no vLLM — that is the point of the Azure path, and why this needs no bid beyond the
# minimum.
#
# Submit with:
#   condor_submit_bid 100 cluster/run_agent1_azure.sub \
#       config="experiments/agent1/configs/agent1_v8_inf_both_gpt54.yaml" \
#       seeds="153 154" out_dir="experiments/agent1/outputs/v8"
#
# Seeds run SEQUENTIALLY inside one job on purpose: gpt-5.4 has a low concurrency ceiling and
# answers 429 when several callers share it (the sj3 judge sweeps hit exactly this). One
# agent1 run is already four assistants against one deployment; parallel seeds on top of that
# is what turns a slow run into a failed one. Want them in parallel — submit separate jobs.
export HOME="${HOME:-/home/jtaraz}"
set -euo pipefail
export PYTHONUNBUFFERED=1

PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"

# Azure creds (AZURE_OPENAI_ENDPOINT / _API_KEY / _API_VERSION) — same source as the sj3/sj4
# gpt-5.4 judges. The client reads them from the environment; nothing is baked into a config.
set -a; source /fast/jtaraz/syco-bench/.env; set +a
[ -n "${AZURE_OPENAI_API_KEY:-}" ] || { echo "FATAL: AZURE_OPENAI_API_KEY unset" >&2; exit 1; }
[ -n "${AZURE_OPENAI_ENDPOINT:-}" ] || { echo "FATAL: AZURE_OPENAI_ENDPOINT unset" >&2; exit 1; }

CONFIG="${1:-experiments/agent1/configs/agent1_v8_inf_both_gpt54.yaml}"
SEEDS="${2:-153}"
OUT_DIR="${3:-experiments/agent1/outputs/v8}"

# The record name carries the deployment, so an Azure run never collides with the OpenRouter
# runs of the same cell sitting in the same directory.
STEM=$(basename "$CONFIG" .yaml | sed 's/^agent1_//')
mkdir -p "$OUT_DIR"

for seed in $SEEDS; do
  OUT="$OUT_DIR/${STEM}_s${seed}.json"
  if [ -f "$OUT" ]; then
    echo "[$(date +%H:%M:%S)] SKIP  $OUT (already present)"
    continue
  fi
  echo "[$(date +%H:%M:%S)] START seed=$seed -> $OUT"
  set +e
  "$PROJECT/.venv/bin/python" -m experiments.agent1.run \
      --config "$CONFIG" --seed "$seed" --out "$OUT"
  rc=$?
  set -e
  # Report the process status AND whether a record landed: a run that dies inside the client's
  # retry ladder writes nothing, and an exit code alone has been misleading before.
  if [ -f "$OUT" ]; then
    echo "[$(date +%H:%M:%S)] DONE  seed=$seed rc=$rc"
  else
    echo "[$(date +%H:%M:%S)] FAIL  seed=$seed rc=$rc — no record written"
  fi
done
echo "[$(date +%H:%M:%S)] ALL SEEDS FINISHED"
