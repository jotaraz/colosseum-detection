#!/bin/bash
# The six optimized arms (ask_r11_1 .. ask_r13_2) on their NATIVE target model, 11 rollouts
# each, then jv10/glm-5.2 over every Priya turn they produce.
#
# "Native" means the model the arm was optimized against — an arm promoted out of run11 is run
# on kimi-k2.6, run12's on glm-5.2, run13's on deepseek-v4-flash-0731. Cross-model transfer is
# a separate question and deliberately not asked here.
#
# Configs are generated from the v15 `_both_` baselines, which already carry exactly the
# serving each agent3 run used, and it differs per cell by history rather than design:
#   deepseek  max_tokens=16000, pinned GMICloud (allow_fallbacks false)
#   kimi      max_tokens=8000,  unpinned
#   glm       max_tokens=8000,  provider order [DeepInfra, Fireworks]
#
# SEED BLOCK 1100-1199, fresh. v15's 400-699 is reserved by run_agent1_pair_arms.sh and
# 700-999 is its v16 block; nothing else on disk uses four digits. Seeds are deterministic per
# (arm, replicate index), so re-running this script tops a short arm up rather than colliding.
#
# RETRY TO 11: a rollout that dies on a provider error leaves no record, so the runner walks
# further into the arm's block until 11 records exist or the block is exhausted. The corpus is
# meant to be balanced across arms; an arm short by one is a silent confound in any comparison.
set -uo pipefail
export HOME="${HOME:-/home/jtaraz}"
export PYTHONUNBUFFERED=1
export SOFTFILELOCK=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"
export PYTHONPATH="$PROJECT"
set -a; source "$PROJECT/.env"; set +a
[ -n "${OPENROUTER_API_KEY:-}" ] || { echo "FATAL: OPENROUTER_API_KEY unset" >&2; exit 1; }
mkdir -p "$PROJECT/logs"
echo "[$(date +%H:%M:%S)] host=$(hostname) proxy=${https_proxy:-<none>}"

PY="$PROJECT/.venv/bin/python"
FIXTURE=v15
WANT="${WANT:-11}"
PAR="${PAR:-6}"
BLOCK_BASE=1100
BLOCK_SIZE=20            # room to retry past failures without leaving the arm's block

ARMS=(ask_r11_1 ask_r11_2 ask_r12_1 ask_r12_2 ask_r13_1 ask_r13_2)
MODELS=(kimi     kimi      glm       glm       deepseek  deepseek)

run_one() {   # $1 = "arm model seed"
  read -r arm model seed <<< "$1"
  cfg="experiments/agent1/configs/agent1_v15_inf_${arm}_${model}.yaml"
  out="experiments/agent1/outputs/v15/inf_${arm}_${model}_s${seed}.json"
  [ -f "$cfg" ] || { echo "[$(date +%H:%M:%S)] NOCFG $cfg"; return 0; }
  [ -f "$out" ] && { echo "[$(date +%H:%M:%S)] SKIP  $out"; return 0; }
  echo "[$(date +%H:%M:%S)] START $arm $model s$seed"
  "$PY" -m experiments.agent1.run --config "$cfg" --seed "$seed" --out "$out" \
      > "$PROJECT/logs/v15_${arm}_${model}_s${seed}.log" 2>&1
  rc=$?
  if [ -f "$out" ]; then echo "[$(date +%H:%M:%S)] DONE  $arm $model s$seed rc=$rc"
  else echo "[$(date +%H:%M:%S)] FAIL  $arm $model s$seed rc=$rc — no record"; fi
}
export -f run_one; export PROJECT PY

# ---- rollouts, retrying into the arm's block until WANT records exist -----------------------
for round in 1 2 3; do
  JOBS=""
  for i in "${!ARMS[@]}"; do
    arm="${ARMS[$i]}"; model="${MODELS[$i]}"; base=$(( BLOCK_BASE + i * BLOCK_SIZE ))
    have=$(ls "experiments/agent1/outputs/v15/inf_${arm}_${model}_s"*.json 2>/dev/null | wc -l)
    need=$(( WANT - have ))
    (( need > 0 )) || { echo "[$(date +%H:%M:%S)] $arm: $have/$WANT — complete"; continue; }
    queued=0
    for s in $(seq "$base" $(( base + BLOCK_SIZE - 1 ))); do
      (( queued < need )) || break
      [ -f "experiments/agent1/outputs/v15/inf_${arm}_${model}_s${s}.json" ] && continue
      JOBS="${JOBS}${arm} ${model} ${s}"$'\n'; queued=$(( queued + 1 ))
    done
  done
  n=$(echo "$JOBS" | grep -cv '^$')
  (( n > 0 )) || { echo "[$(date +%H:%M:%S)] all arms at $WANT after round $((round-1))"; break; }
  echo "[$(date +%H:%M:%S)] round $round: $n rollouts queued (PAR=$PAR)"
  echo "$JOBS" | grep -v '^$' | xargs -P "$PAR" -I{} bash -c 'run_one "$@"' _ {}
done

echo "=== rollout tally ==="
for i in "${!ARMS[@]}"; do
  arm="${ARMS[$i]}"; model="${MODELS[$i]}"
  echo "  $arm ($model): $(ls experiments/agent1/outputs/v15/inf_${arm}_${model}_s*.json 2>/dev/null | wc -l)/$WANT"
done

# ---- jv10 / glm-5.2 over every Priya turn of what was produced ------------------------------
TARGETS=experiments/agent2/jv10_optimized_arms_targets.json
"$PY" - "$TARGETS" <<'PYEOF'
import json, re, sys, glob
from pathlib import Path
from experiments.agent1.workspace import Workspace
from experiments.agent2.target_run import assemble_turns
out = []
for p in sorted(glob.glob('experiments/agent1/outputs/v15/inf_ask_r1*_s*.json')):
    if any(x in p for x in ('.deception', '.sweep', '.category', '.lie')):
        continue
    d = json.loads(Path(p).read_text()); c = d.get('config')
    if not c:
        continue
    ws = Workspace.load(Path(c['workspace']))
    arm = re.match(r'inf_(ask_r\d+_\d+)_', Path(p).name).group(1)
    for t in assemble_turns(d, ws):
        if t.get('agent') == 'Priya':
            out.append({"run": p, "agent": "Priya", "turn_index": t['turn_index'],
                        "model": c['model_name'].split('/')[-1], "ask": arm})
Path(sys.argv[1]).write_text(json.dumps(out, indent=1))
print(f"  {len(out)} Priya turns queued for jv10")
PYEOF

# --workers 8, raised from 3 on 2026-08-26 for the same reason as the v15 sweep: measured over
# ~900 calls that night, workers 8 on this pinned pool returned ZERO 429s and ran ~3x the
# throughput of workers 3. The rollout phase above re-runs on a resume but skips every record
# already on disk, so raising this and resubmitting costs only the judge units in flight.
COMMON=(--targets "$TARGETS" --judge-version jv10 --judge-model z-ai/glm-5.2
        --pin-provider DeepInfra --workers 8 --max-tokens 16000
        --selection-label "optimized arms (ask_r11_1..ask_r13_2) on native models: full Priya sweep")
for R in 1 2; do echo "=== sweep replicate $R ==="; "$PY" -m experiments.agent2.lie_over_agent1 "${COMMON[@]}" --replicate "$R" || true; done
for R in 1 2; do echo "=== repair replicate $R ==="; "$PY" -m experiments.agent2.lie_over_agent1 "${COMMON[@]}" --replicate "$R" --repair || true; done
echo "[$(date +%H:%M:%S)] OPTIMIZED ARMS BATCH FINISHED"
