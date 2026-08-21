#!/bin/bash
# agent1 rollouts with an OpenRouter model as the agent model (glm / deepseek / kimi).
#
# CPU-only: the whole job is API wall-clock, four assistants taking turns against OpenRouter.
# The azure twin (run_agent1_azure.sh) exists because its creds and 429 behaviour differ;
# this one sources the repo .env for OPENROUTER_API_KEY and nothing else.
#
# Submit with:
#   condor_submit_bid 100 cluster/run_agent1_openrouter.sub \
#       config="experiments/agent1/configs/agent1_v13_inf_askF_glm.yaml" \
#       seeds="208 209" out_dir="experiments/agent1/outputs/v13"
#
# Seeds run sequentially inside one job; submit separate jobs for parallelism.
export HOME="${HOME:-/home/jtaraz}"
set -euo pipefail
export PYTHONUNBUFFERED=1

PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"

# OpenRouter creds only — deliberately NOT syco-bench/.env, which sets AZURE_OPENAI_ENDPOINT
# and would flip provider="auto" callers to Azure (see run_social_jira3_deception_judge.sh).
set -a; source "$PROJECT/.env"; set +a
[ -n "${OPENROUTER_API_KEY:-}" ] || { echo "FATAL: OPENROUTER_API_KEY unset" >&2; exit 1; }

CONFIG="${1:?config path required}"
SEEDS="${2:?seeds required}"
OUT_DIR="${3:?out_dir required}"

STEM=$(basename "$CONFIG" .yaml | sed 's/^agent1_v[0-9]*_//')
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
  if [ -f "$OUT" ]; then
    echo "[$(date +%H:%M:%S)] DONE  seed=$seed rc=$rc"
  else
    echo "[$(date +%H:%M:%S)] FAIL  seed=$seed rc=$rc — no record written"
  fi
done
echo "[$(date +%H:%M:%S)] ALL SEEDS FINISHED"
