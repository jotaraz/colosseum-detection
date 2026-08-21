#!/bin/bash
# agent2 CRITIC_CATEGORY (alone — no SOUNDNESS/AT-STAKE) over finished agent1 run records,
# judged by Azure gpt-5.4. See experiments/agent2/category_over_agent1.py for what is bridged
# and how the missing per-turn uptake ledger is reconstructed.
#
# CPU-only: pure API wall-clock. Submit with:
#   condor_submit_bid 100 cluster/run_agent2_category_agent1.sub
#
# Run files are passed as arguments; default is the single askA_glm_s119 run this was built for.
export HOME="${HOME:-/home/jtaraz}"
set -euo pipefail
export PYTHONUNBUFFERED=1

PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"

# Azure creds (AZURE_OPENAI_ENDPOINT / _API_KEY / _API_VERSION) — same source as the sj3
# gpt-5.4 judge sweeps.
set -a; source /fast/jtaraz/syco-bench/.env; set +a
[ -n "${AZURE_OPENAI_API_KEY:-}" ] || { echo "FATAL: AZURE_OPENAI_API_KEY unset" >&2; exit 1; }

RUNS=("$@")
[ ${#RUNS[@]} -gt 0 ] || RUNS=(experiments/agent1/outputs/v6_ask/askA_glm_s119.json)

# workers=4: gpt-5.4 has a low concurrency ceiling (429s) — see the sj3 judge memory; one run
# is only ~16 calls anyway.
exec "$PROJECT/.venv/bin/python" -m experiments.agent2.category_over_agent1 \
    "${RUNS[@]}" \
    --provider azure --judge-model gpt-5.4 --workers 4
