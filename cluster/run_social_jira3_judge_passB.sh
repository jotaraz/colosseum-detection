#!/bin/bash
# CPU-only orchestrator for PASS (B): fresh judge of the never-judged v5_quit23 trees, at the safe
# LOW concurrency (workers 8). Self-driving via filesystem signals (no condor calls, so it works on
# an execute node). Sequence, per the plan:
#   1. Wait for the running repair loop (17392930) to finish  -> avoid deployment contention.
#   2. Judge qwen-nodecoy + glm-decoy-ON (fresh), then repair-loop each to 0 residual.
#   3. Wait (as long as needed) for glm-nodecoy ROLLOUT generation to complete, then judge it clean.
# Each tree is fresh-judged with --skip-existing then repair-swept until parse_errors hit 0 (or 3
# no-progress sweeps => persistent non-429 failures).
set -eo pipefail
export HOME="${HOME:-/home/jtaraz}"
export PYTHONUNBUFFERED=1

PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT/experiments/social_jira3"
set -a; source /fast/jtaraz/syco-bench/.env; set +a
PY="$PROJECT/.venv/bin/python"

QWEN_ND=outputs/social_jira3_c2p2_qwen3_6_35b_a3b_conflict_quit23_v5_confsweep_nodecoy
GLM_DON=outputs/social_jira3_c2p2_glm_4_7_flash_conflict_quit23_v5_confsweep
GLM_ND=outputs/social_jira3_c2p2_glm_4_7_flash_conflict_quit23_v5_confsweep_nodecoy
LOOPLOG=/fast/jtaraz/cluster_stuff/judge/repairLoop_17392930.out

ts(){ date '+%m-%d %H:%M:%S'; }
count_pe(){ "$PY" -c "import glob,json,sys;print(sum(1 for e in sys.argv[1:] for f in glob.glob(e+'/**/judge_results.json',recursive=True) for t in json.load(open(f)).get('turns',[]) if t.get('parse_error')))" "$@"; }

judge_clean(){  # $1=label, rest=tree dirs
  local label="$1"; shift
  echo "[orch $(ts)] === FRESH JUDGE: $label ==="
  "$PY" -u judge.py "$@" --skip-existing --workers 8 --max-retries 8 || true
  local prev=-1 noprog=0 rem
  for i in $(seq 1 20); do
    "$PY" -u judge.py "$@" --repair --workers 8 --max-retries 5 || true
    rem=$(count_pe "$@")
    echo "[orch $(ts)] $label repair sweep $i -> remaining_parse_errors=$rem"
    [ "$rem" -eq 0 ] && { echo "[orch $(ts)] $label CLEAN"; return 0; }
    if [ "$rem" -eq "$prev" ]; then noprog=$((noprog+1)); else noprog=0; fi
    [ "$noprog" -ge 3 ] && { echo "[orch $(ts)] $label STALLED at $rem (persistent, not 429)"; return 0; }
    prev=$rem; sleep 30
  done
}

# 1) wait for the repair loop to finish (its log ends with "[loop] FINAL"); cap ~4h
echo "[orch $(ts)] waiting for repair loop (17392930) to finish..."
for i in $(seq 1 240); do
  grep -q "\[loop\] FINAL" "$LOOPLOG" 2>/dev/null && { echo "[orch $(ts)] repair loop finished."; break; }
  sleep 60
done

# 2) qwen-nodecoy + glm-decoy-ON
judge_clean "qwen-nodecoy + glm-decoyON" "$QWEN_ND" "$GLM_DON"

# 3) wait for glm-nodecoy generation to COMPLETE, then judge it
GLM_ND_TS=$(ls -d $GLM_ND/*/ 2>/dev/null | head -1)
echo "[orch $(ts)] waiting for glm-nodecoy generation to complete (status=completed)..."
for i in $(seq 1 288); do  # up to ~24h
  st=$("$PY" -c "import json;print(json.load(open('${GLM_ND_TS}progress.json')).get('status'))" 2>/dev/null || echo "?")
  [ "$st" = "completed" ] && { echo "[orch $(ts)] glm-nodecoy generation complete."; break; }
  sleep 300
done
judge_clean "glm-nodecoy" "$GLM_ND"

echo "[orch $(ts)] ALL PASS-B DONE. residual across B trees: $(count_pe "$QWEN_ND" "$GLM_DON" "$GLM_ND")"
