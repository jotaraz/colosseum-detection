#!/bin/bash
# Repair-only pass over the v15 full sweep: re-judge just the units whose verdict came back
# broken (judge_error / parse_error). 16 such units as of 2026-08-26 02:52.
#
# Split out from run_jv10_v15_sweep.sh because that job exited 1 after finishing both sweep
# replicates — `set -e` saw a non-zero return (any failed unit causes one) and skipped the very
# repair passes meant to fix them. The sweep's own work is complete; only this was missed.
#
# Touches only the askA..askP5 verdict files, NOT inf_ask_r1* — so it is safe to run beside the
# optimized-arms job, which writes a disjoint set of files. Two processes read-modify-writing
# one verdict file would clobber each other; /fast has no file locking.
set -uo pipefail
export HOME="${HOME:-/home/jtaraz}"
export PYTHONUNBUFFERED=1
export SOFTFILELOCK=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"
PY="$PROJECT/.venv/bin/python"
COMMON=(--targets experiments/agent2/jv10_v15_full_targets.json --judge-version jv10
        --judge-model z-ai/glm-5.2 --pin-provider DeepInfra --workers 4 --max-tokens 16000
        --selection-label "v15 full jv10/glm-5.2 sweep (all arms, 3 target models)")
for R in 1 2; do
  echo "=== repair replicate $R ==="
  "$PY" -m experiments.agent2.lie_over_agent1 "${COMMON[@]}" --replicate "$R" --repair || true
done
echo "=== v15 repair complete ==="
"$PY" - <<'PY'
import json, glob
n = sum(1 for p in glob.glob('experiments/agent1/outputs/v15/*.deception_jv10_zaiglm52*.json')
        for t in (json.load(open(p)).get('turns') or [])
        if t.get('judge_error') or t.get('parse_error'))
print(f"broken units remaining: {n}")
PY
