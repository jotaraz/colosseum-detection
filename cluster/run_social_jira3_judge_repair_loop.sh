#!/bin/bash
# CPU-only HTCondor job: AUTO-LOOP low-concurrency repair until 0 residual failures.
# Each sweep re-judges only the still-failed (parse_error) turns and merges back; on a bursty
# shared deployment, successive fast sweeps retry the residual in different load windows and
# converge much faster than one slow high-retry pass. Low --max-retries so doomed calls give up
# quickly instead of hogging a worker for ~10 min. Stops on 0 residual, 3 no-progress sweeps
# (=> remaining failures are persistent, not transient 429s), or MAXSWEEPS.
#
# args: $1=workers (default 8)  $2=max-retries (default 5)  $3=max sweeps (default 30)
set -eo pipefail
export HOME="${HOME:-/home/jtaraz}"
export PYTHONUNBUFFERED=1
WORKERS="${1:-8}"; RETRIES="${2:-5}"; MAXSWEEPS="${3:-30}"

PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT/experiments/social_jira3"
set -a; source /fast/jtaraz/syco-bench/.env; set +a
PY="$PROJECT/.venv/bin/python"

TREES="outputs/social_jira3_c2p2_gptoss_120b_medium_conflict_quit23_v5_confsweep \
outputs/social_jira3_c2p2_gptoss_120b_medium_conflict_quit23_v5_confsweep_nodecoy \
outputs/social_jira3_c2p2_qwen3_6_35b_a3b_conflict_quit23_v5_confsweep \
outputs/social_jira3_c2p2_glm_4_7_flash_conflict_quit23_v5_confsweep \
outputs/social_jira3_c2p2_glm_4_7_flash_conflict_quit23_v5_confsweep_nodecoy \
outputs/social_jira3_c2p2_qwen3_6_35b_a3b_conflict_quit23_v5_confsweep_nodecoy"

count(){ "$PY" -c "import glob,json;print(sum(1 for f in glob.glob('outputs/social_jira3_c2p2_*quit23_v5*/**/judge_results.json',recursive=True) for t in json.load(open(f)).get('turns',[]) if t.get('parse_error')))"; }

prev=-1; noprog=0
echo "[loop] host=$(hostname) workers=$WORKERS retries=$RETRIES start_remaining=$(count)"
for i in $(seq 1 "$MAXSWEEPS"); do
  echo "=== sweep $i (workers=$WORKERS retries=$RETRIES) ==="
  "$PY" -u judge.py $TREES --repair --workers "$WORKERS" --max-retries "$RETRIES" || true
  rem=$(count)
  echo "=== sweep $i complete: remaining_parse_errors=$rem ==="
  [ "$rem" -eq 0 ] && { echo "[loop] ALL_CLEAR after $i sweep(s)"; break; }
  if [ "$rem" -eq "$prev" ]; then noprog=$((noprog+1)); else noprog=0; fi
  [ "$noprog" -ge 3 ] && { echo "[loop] STALLED at remaining=$rem after 3 no-progress sweeps"; break; }
  prev=$rem
  sleep 60
done
echo "[loop] FINAL remaining_parse_errors=$(count)"
