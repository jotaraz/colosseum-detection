#!/bin/bash
# job_appl4 public-speaking sweep on the cluster (CPU-only; all LLM calls go to
# OpenRouter over HTTPS). One invocation runs ONE batch config, passed as the
# config short-name argument: gptoss | qwen36 | qwen3-32b
# -> experiments/job_appl4/configs/ps_sweep_<name>.json
#
# These are the reruns of the 2026-07-07 public-speaking mini-sweep under the
# NEW dossier prompt ("this professional skill is the ONLY one they have").
export HOME="${HOME:-/home/jtaraz}"
set -euo pipefail
export PYTHONUNBUFFERED=1

CFG="${1:?usage: run_job_appl4_ps_sweeps.sh <gptoss|qwen36|qwen3-32b>}"
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
VENV="$PROJECT/.venv"

cd "$PROJECT/experiments/job_appl4"

if [ ! -x "$VENV/bin/python" ]; then
    echo "ERROR: $VENV/bin/python missing — build the repo venv first" >&2
    exit 1
fi
"$VENV/bin/python" -c "import requests; print('requests OK')"

# sanity: this run must use the new only-skill dossier prompt
grep -q "ONLY professional skill" prompts.py \
    || { echo "ERROR: prompts.py lacks the only-skill dossier constraint — stale sync?" >&2; exit 1; }

CONFIG="configs/ps_sweep_${CFG}.json"
[ -f "$CONFIG" ] || { echo "ERROR: $CONFIG not found" >&2; exit 1; }

echo "running job_appl4 public-speaking batch: $CONFIG (new dossier regime)"
"$VENV/bin/python" -u run_batch.py "$CONFIG" --parallel 4
echo "done: $CONFIG"
