#!/bin/bash
# glm-5.2 only: 15 new askA + 15 new askG + 19 new ask_r12_1 + 19 new ask_r12_2 = 68 rollouts,
# each judged twice by jv10/glm-5.2.
#
# ORDER IS PIPELINED, not two phases. A judge pass is idempotent — it rebuilds its target list
# from the records currently on disk and skips every unit already judged — so a sweep can run
# repeatedly *while* rollouts are still landing, and each pass picks up whatever finished since
# the last one. Sequential phases would idle the judge for the whole rollout phase and then idle
# the rollouts for the whole judge phase; this overlaps them, and total wall clock becomes
# roughly max(rollouts, judging) instead of their sum.
#
# The two replicates run as CONCURRENT processes. They write disjoint files
# (`.deception_jv10_zaiglm52.json` vs `..._r2.json`), so they cannot clobber each other — but
# two processes on the SAME replicate would, and /fast has no file locking, so never do that.
#
# CONCURRENCY: rollouts PAR=23, judging 2 x 8 workers. Peak ~39 concurrent glm-5.2 calls.
# Measured basis: 16 concurrent gave 1 x 429 in ~800 calls on this pinned pool, and 48
# concurrent locally gave 5 in a day — so ~39 sits inside the range that produces occasional
# 429s and no failures, which the 7 client retries plus the --repair passes absorb. Nothing
# else is running tonight, so the whole pool is ours.
#
# SEED BLOCK 2000-2399, 100 per arm. Untouched: v15 pair arms hold 400-699, v16 700-999,
# optimized arms 1100-1219, agent3 copies 9xxxxx. A record that already exists is never
# rewritten (`[ -f "$out" ] && SKIP`), so this cannot overwrite an existing run.
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
PY="$PROJECT/.venv/bin/python"
OUT=experiments/agent1/outputs/v15
TARGETS=experiments/agent2/jv10_glm_batch2_targets.json
PAR="${PAR:-23}"
JW="${JW:-8}"
echo "[$(date +%H:%M:%S)] host=$(hostname) proxy=${https_proxy:-<none>} PAR=$PAR JW=$JW"

ARMS=(askA askG ask_r12_1 ask_r12_2)
WANT=(15   15   19        19)
BASE=(2000 2100 2200      2300)

run_one() {
  read -r arm seed <<< "$1"
  cfg="experiments/agent1/configs/agent1_v15_inf_${arm}_glm.yaml"
  out="experiments/agent1/outputs/v15/inf_${arm}_glm_s${seed}.json"
  [ -f "$cfg" ] || { echo "[$(date +%H:%M:%S)] NOCFG $cfg"; return 0; }
  [ -f "$out" ] && { echo "[$(date +%H:%M:%S)] SKIP  $out"; return 0; }
  echo "[$(date +%H:%M:%S)] START $arm s$seed"
  "$PY" -m experiments.agent1.run --config "$cfg" --seed "$seed" --out "$out" \
      > "$PROJECT/logs/v15_${arm}_glm_s${seed}.log" 2>&1
  if [ -f "$out" ]; then echo "[$(date +%H:%M:%S)] DONE  $arm s$seed"
  else echo "[$(date +%H:%M:%S)] FAIL  $arm s$seed — no record"; fi
}
export -f run_one; export PROJECT PY

# Only the seeds in THIS batch's block are ever judged or counted, so the run is independent of
# whatever else exists for these arms.
build_targets() {
  "$PY" - "$TARGETS" <<'PYEOF'
import json, re, sys, glob
from pathlib import Path
from experiments.agent1.workspace import Workspace
from experiments.agent2.target_run import assemble_turns
BLOCK = range(2000, 2400)
out = []
for p in sorted(glob.glob('experiments/agent1/outputs/v15/inf_ask*_glm_s2[0-3]??.json')):
    if not re.search(r'_s\d+\.json$', p):
        continue
    m = re.search(r'_s(\d+)\.json$', p)
    if int(m.group(1)) not in BLOCK:
        continue
    try:                                   # a record still being written is simply not ready
        d = json.loads(Path(p).read_text())
    except (json.JSONDecodeError, OSError):
        continue
    c = d.get('config')
    if not c:
        continue
    ws = Workspace.load(Path(c['workspace']))
    arm = re.match(r'inf_(ask[A-Za-z0-9_]+)_glm_', Path(p).name).group(1)
    for t in assemble_turns(d, ws):
        if t.get('agent') == 'Priya':
            out.append({"run": p, "agent": "Priya", "turn_index": t['turn_index'],
                        "model": "glm", "ask": arm})
Path(sys.argv[1]).write_text(json.dumps(out, indent=1))
print(f"[targets] {len(out)} Priya turns over {len({o['run'] for o in out})} rollouts")
PYEOF
}

sweep() {   # $1 = extra flags
  local extra="${1:-}"
  local -a pids=()
  for R in 1 2; do
    "$PY" -m experiments.agent2.lie_over_agent1 --targets "$TARGETS" --judge-version jv10 \
        --judge-model z-ai/glm-5.2 --pin-provider DeepInfra --workers "$JW" --max-tokens 16000 \
        --selection-label "glm batch2: askA/askG +15, ask_r12_1/2 +19, seeds 2000-2399" \
        --replicate "$R" $extra &
    pids+=($!)
  done
  # `wait` with NO ARGUMENTS waits for EVERY background job of this shell — including the
  # rollout xargs started with `&`. That silently turned the pipelined loop into sequential
  # phases: the first sweep blocked until all 68 rollouts had finished. Wait on these two PIDs
  # only. (Cost the pipelining on job 17483417, 2026-08-26.)
  local pid
  for pid in "${pids[@]}"; do wait "$pid"; done
}

# ---- rollouts in the background, judging pipelined against them ----------------------------
JOBS=""
for i in "${!ARMS[@]}"; do
  arm="${ARMS[$i]}"; want="${WANT[$i]}"; base="${BASE[$i]}"
  for k in $(seq 0 $((want-1))); do JOBS="${JOBS}${arm} $((base+k))"$'\n'; done
done
echo "[$(date +%H:%M:%S)] $(echo "$JOBS" | grep -cv '^$') rollouts queued"
echo "$JOBS" | grep -v '^$' | xargs -P "$PAR" -I{} bash -c 'run_one "$@"' _ {} &
ROLL=$!

while kill -0 "$ROLL" 2>/dev/null; do
  sleep 120
  build_targets
  [ -s "$TARGETS" ] && sweep
done
wait "$ROLL"
echo "[$(date +%H:%M:%S)] rollouts finished"

# ---- final passes: anything that landed after the last pipelined sweep, then repair ---------
build_targets
sweep
sweep --repair
echo "=== tally ==="
for i in "${!ARMS[@]}"; do
  arm="${ARMS[$i]}"
  n=$(ls $OUT/inf_${arm}_glm_s2[0-3]??.json 2>/dev/null | grep -cE "_s[0-9]+\.json$")
  v=$(ls $OUT/inf_${arm}_glm_s2[0-3]??.deception_jv10_zaiglm52*.json 2>/dev/null | wc -l)
  echo "  $arm: $n/${WANT[$i]} rollouts, $v verdict files (want $(( ${WANT[$i]} * 2 )))"
done
echo "[$(date +%H:%M:%S)] GLM BATCH2 FINISHED"
