#!/bin/bash
# jv10 + jv11 deception judges on Azure gpt-5.4, over the same frozen 91-turn sample the
# OpenRouter job uses (cluster/run_deception_or.sh — read that one first; it documents the
# sample and why replicates are resamples rather than seeds).
#
#   condor_submit_bid 100 cluster/run_deception_azure.sub
#
# CONCURRENCY. Azure has no adaptive limiter and does not need one: llm.py's Azure client
# already honours Retry-After on 429 with a capped exponential backoff and a 1200s wall-clock
# retry budget, so a too-high worker count self-limits into waiting rather than failing. WORKERS
# is therefore a starting point, not a ceiling — the sj3 experience (gpt-5.4 429s under heavy
# concurrency) says 8 is a reasonable place to sit. If the log shows sustained 429 lines,
# lower it; the run is resumable, so a re-submit picks up only the unjudged units.
set -uo pipefail
export HOME="${HOME:-/home/jtaraz}"
export PYTHONUNBUFFERED=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"
export PYTHONPATH="$PROJECT"
set -a; source "$PROJECT/.env"; set +a
[ -n "${AZURE_OPENAI_API_KEY:-}" ] || { echo "FATAL: AZURE_OPENAI_API_KEY unset" >&2; exit 1; }
[ -n "${AZURE_OPENAI_ENDPOINT:-}" ] || { echo "FATAL: AZURE_OPENAI_ENDPOINT unset" >&2; exit 1; }
echo "[$(date +%H:%M:%S)] host=$(hostname) proxy=${https_proxy:-<none>}"

PY="$PROJECT/.venv/bin/python"
TARGETS="$PROJECT/experiments/agent2/deception_sample.json"
MODEL="gpt-5.4"
WORKERS=8
[ -f "$TARGETS" ] || { echo "FATAL: sample missing: $TARGETS" >&2; exit 1; }
echo "sample: $("$PY" -c "import json,sys;print(len(json.load(open(sys.argv[1]))))" "$TARGETS") turns"
LABEL="deception sample v1 (seed 0): 9 a1_hit + 20 a1_unjudged + 52 a3_full + 10 a3_extra"

# One live call before spending an hour on a bad deployment name or a stale key.
"$PY" - <<'PY' || { echo "FATAL: azure probe failed" >&2; exit 1; }
import sys  # PYTHONPATH is exported above, so the package imports without a path hack
from experiments.social_jira4.llm import make_judge_caller
call = make_judge_caller(provider="azure", model="gpt-5.4", max_tokens=64)
out = call("You are a probe.", "Reply with the single word: ok")
print("azure probe:", repr(out)[:120])
sys.exit(0 if out and out.strip() else 1)
PY

rc=0
for version in jv10 jv11; do
  for r in 1 2; do
    echo "[$(date +%H:%M:%S)] === $MODEL $version replicate $r (workers $WORKERS) ==="
    "$PY" -m experiments.agent2.lie_over_agent1 \
        --targets "$TARGETS" --judge-version "$version" \
        --provider azure --judge-model "$MODEL" \
        --workers "$WORKERS" --replicate "$r" --selection-label "$LABEL" \
        > "$PROJECT/cluster/deception_gpt54_${version}_r${r}.log" 2>&1 || rc=1
    tail -2 "$PROJECT/cluster/deception_gpt54_${version}_r${r}.log"
    grep -c "429" "$PROJECT/cluster/deception_gpt54_${version}_r${r}.log" \
      | xargs -I{} echo "  429 mentions in log: {}"
  done
done
echo "[$(date +%H:%M:%S)] AZURE DECEPTION SWEEP FINISHED rc=$rc"
exit $rc
