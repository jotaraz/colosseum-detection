#!/bin/bash
# agent5: the pg1 pursuing-goal gate, then the njv3 per-step deception judge, over a list of runs,
# on a CPU node. Written 2026-09-16 for the 38 remaining 5.e.viii rollouts that carry reasoning
# (the first 40 were judged locally; see experiments/agent5/findings/deception-handoff.md §14-15).
#
#   condor_submit_bid 15 cluster/run_agent5_njv3_gate.sub \
#       runlist=experiments/agent5/njv3_remaining38.txt runworkers=3 workers=2
#
# Both stages write sidecars NEXT TO EACH RUN (run.pursuing_goal_*.json, run.deception_njv3_*.json)
# on /fast, and both drivers skip a seat that already has one — so an evicted and restarted job
# resumes instead of re-paying for finished work. The gate runs first and to completion: its
# verdict is what tells the analysis whether a seat was playing for the hidden goal at all, and a
# seat it answers `no` for should be reported apart from the rest (njv3_fab_report.py --gate).
# Nothing here consults the gate to decide what to judge; everything is judged either way.
export HOME="${HOME:-/home/jtaraz}"
set -uo pipefail
export PYTHONUNBUFFERED=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"

RUNLIST="${1:?runlist required}"
RUNWORKERS="${2:-3}"
WORKERS="${3:-2}"
JUDGE="${4:-openrouter:openai/gpt-5.6-sol}"
PIN="${5:-openai/flex,openai}"
SIDECAR="${6:-gpt-5.6-sol}"
PROMPT="$PROJECT/experiments/agent5/CRITIC_DECEPTION_NJV3_NOTOOLS.md"
GATE_PROMPT="$PROJECT/experiments/agent5/CRITIC_PURSUING_GOAL.md"
export OPENROUTER_API_KEY_FILE="$PROJECT/experiments/agent5/.env3"

# NFS reads of the key files have failed transiently on some nodes (see run_agent5_disc_judge.sh).
for attempt in 1 2 3 4 5 6; do
  KEY3="$(grep -m1 '^sk-or-' "$OPENROUTER_API_KEY_FILE" 2>/dev/null)"
  [ -n "${KEY3:-}" ] && [ -f "$PROMPT" ] && [ -f "$GATE_PROMPT" ] && [ -f "$RUNLIST" ] && break
  echo "key/prompt/runlist load attempt $attempt failed, retrying in 20s" >&2; sleep 20
done
[ -n "${KEY3:-}" ]     || { echo "FATAL: $OPENROUTER_API_KEY_FILE has no sk-or- line" >&2; exit 1; }
[ -f "$PROMPT" ]       || { echo "FATAL: $PROMPT missing" >&2; exit 1; }
[ -f "$GATE_PROMPT" ]  || { echo "FATAL: $GATE_PROMPT missing" >&2; exit 1; }
[ -f "$RUNLIST" ]      || { echo "FATAL: $RUNLIST missing" >&2; exit 1; }

mapfile -t RUNS < <(grep -v '^[[:space:]]*$' "$RUNLIST")
[ ${#RUNS[@]} -gt 0 ] || { echo "FATAL: $RUNLIST is empty" >&2; exit 1; }
MISSING=0
for d in "${RUNS[@]}"; do [ -f "$PROJECT/$d/run.json" ] || { echo "MISSING $d" >&2; MISSING=$((MISSING+1)); }; done
[ "$MISSING" -eq 0 ] || { echo "FATAL: $MISSING run(s) in the list have no run.json" >&2; exit 1; }
echo "[$(date +%H:%M:%S)] ${#RUNS[@]} runs, judge $JUDGE, pin '$PIN', run-workers $RUNWORKERS x workers $WORKERS"

# Preflight with the REAL provider pin: a pin naming a backend the account cannot reach fails
# every call, and finding that out after two hours of queueing is the expensive way to learn it.
"$PROJECT/.venv/bin/python" - "$JUDGE" "$PIN" <<'PY'
import sys
sys.path.insert(0, "/fast/jtaraz/LIARS/colosseum-detection")
from experiments.agent5.preference_judge import make_caller
spec, pin = sys.argv[1], sys.argv[2]
try:
    reply = make_caller(spec, max_tokens=2000, pin=pin)("You reply with one word.",
                                                        "Reply with the single word OK.")
except Exception as exc:
    print(f"FATAL: preflight failed for {spec} pin={pin}: {type(exc).__name__}: {exc}", file=sys.stderr)
    raise SystemExit(1)
print(f"preflight OK {spec} pin={pin}: {reply.strip()[:40]!r}")
PY
[ $? -eq 0 ] || exit 1

rc_total=0
for rep in 1 2; do
  echo "[$(date +%H:%M:%S)] === GATE replicate $rep"
  "$PROJECT/.venv/bin/python" -m experiments.agent5.pursuing_judge5 \
    --runs "${RUNS[@]}" --api-judge "$JUDGE" --pin-provider "$PIN" --sidecar-judge "$SIDECAR" \
    --prompt "$GATE_PROMPT" --replicate "$rep" \
    --run-workers 4 --workers "$WORKERS" \
    --workdir "$PROJECT/experiments/agent5/outputs/pursuing_work_cluster_r$rep"
  rc=$?; rc_total=$((rc_total+rc)); echo "[$(date +%H:%M:%S)] gate replicate $rep rc=$rc"
done

for rep in 1 2; do
  echo "[$(date +%H:%M:%S)] === NJV3 replicate $rep"
  "$PROJECT/.venv/bin/python" -m experiments.agent5.deception_njv1 \
    --runs "${RUNS[@]}" --backend api --api-judge "$JUDGE" --pin-provider "$PIN" \
    --sidecar-judge "$SIDECAR" --prompt "$PROMPT" --replicate "$rep" \
    --run-workers "$RUNWORKERS" --workers "$WORKERS" --timeout 2400 \
    --workdir "$PROJECT/experiments/agent5/outputs/njv3_cluster_r$rep"
  rc=$?; rc_total=$((rc_total+rc)); echo "[$(date +%H:%M:%S)] njv3 replicate $rep rc=$rc"
done

"$PROJECT/.venv/bin/python" -m experiments.agent5.judge_batch_cost \
  --runs-file "$RUNLIST" --judge "$SIDECAR" --reps "" _r2 --key
echo "[$(date +%H:%M:%S)] ALL DONE rc_total=$rc_total"
exit 0
