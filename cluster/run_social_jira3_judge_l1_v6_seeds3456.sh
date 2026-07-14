#!/bin/bash
# CPU-only LEVEL-1 TURN JUDGE (the "indicator" judge) for the fresh v6 seeds3456 rollout tree.
# Default judge.py pass: flags phenomenon indicators per turn -> judge_results.json per leaf.
# Fresh-judge with --skip-existing (idempotent), then repair-sweep parse_errors to 0 (or bail
# after 3 no-progress sweeps => persistent non-429 failures). Azure/gpt-5.4 via syco-bench/.env.
#   condor_submit_bid 50 cluster/run_social_jira3_judge_l1_v6_seeds3456.sub
set -eo pipefail
export HOME="${HOME:-/home/jtaraz}"
export PYTHONUNBUFFERED=1

PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT/experiments/social_jira3"
set -a; source /fast/jtaraz/syco-bench/.env; set +a
PY="$PROJECT/.venv/bin/python"

TREE=outputs/social_jira3_c2p2_qwen3_6_35b_a3b_conflict_quit23_v6_confsweep_seeds3456

ts(){ date '+%m-%d %H:%M:%S'; }
count_pe(){ "$PY" -c "import glob,json,sys;print(sum(1 for e in sys.argv[1:] for f in glob.glob(e+'/**/judge_results.json',recursive=True) for t in json.load(open(f)).get('turns',[]) if t.get('parse_error')))" "$@"; }

echo "[judge $(ts)] === FRESH LEVEL-1 TURN JUDGE: $TREE ==="
"$PY" -u judge.py "$TREE" --skip-existing --workers 8 --max-retries 8 || true

prev=-1; noprog=0
for i in $(seq 1 20); do
  "$PY" -u judge.py "$TREE" --repair --workers 8 --max-retries 5 || true
  rem=$(count_pe "$TREE")
  echo "[judge $(ts)] repair sweep $i -> remaining_parse_errors=$rem"
  [ "$rem" -eq 0 ] && { echo "[judge $(ts)] CLEAN"; break; }
  if [ "$rem" -eq "$prev" ]; then noprog=$((noprog+1)); else noprog=0; fi
  [ "$noprog" -ge 3 ] && { echo "[judge $(ts)] STALLED at $rem (persistent, not 429)"; break; }
  prev=$rem; sleep 30
done
echo "[judge $(ts)] DONE. residual parse_errors: $(count_pe "$TREE")"
