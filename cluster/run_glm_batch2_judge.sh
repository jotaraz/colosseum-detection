#!/bin/bash
# Judge-only companion to job 17483417: sweeps the batch-2 glm rollouts (seeds 2000-2399) with
# jv10/glm-5.2 x2 WHILE that job is still producing them.
#
# Why it exists: 17483417's own judge loop is parked. Its `sweep()` ended in a bare `wait`,
# which waits for every background job of the shell — including the rollout xargs — so the
# first sweep blocked until all 68 rollouts finish. The rollouts are unaffected and still
# landing; only the overlap was lost. This job restores it from outside.
#
# It uses its OWN targets file. Sharing 17483417's would race on the write.
#
# The parked sweep in 17483417 wakes the moment its rollouts finish, and would then write the
# same verdict files this job writes — so 17483417 is killed at that transition (all rollouts
# done, nothing in flight, nothing wasted). Until then the two cannot collide, because the
# parked one is blocked.
set -uo pipefail
export HOME="${HOME:-/home/jtaraz}"
export PYTHONUNBUFFERED=1
export SOFTFILELOCK=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"
export PYTHONPATH="$PROJECT"
set -a; source "$PROJECT/.env"; set +a
PY="$PROJECT/.venv/bin/python"
TARGETS=experiments/agent2/jv10_glm_batch2_judge_targets.json
JW="${JW:-8}"
WANT_ROLLOUTS="${WANT_ROLLOUTS:-68}"
IDLE_LIMIT="${IDLE_LIMIT:-10}"     # consecutive dry passes before giving up

build_targets() {
  "$PY" - "$TARGETS" <<'PYEOF'
import json, re, sys, glob
from pathlib import Path
from experiments.agent1.workspace import Workspace
from experiments.agent2.target_run import assemble_turns
out = []
for p in sorted(glob.glob('experiments/agent1/outputs/v15/inf_ask*_glm_s2[0-3]??.json')):
    if not re.search(r'_s\d+\.json$', p):
        continue
    try:                                   # a record mid-write is simply not ready yet
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
print(f"[targets] {len(out)} turns over {len({o['run'] for o in out})} rollouts")
PYEOF
}

sweep() {
  local extra="${1:-}"
  local -a pids=()
  for R in 1 2; do
    "$PY" -m experiments.agent2.lie_over_agent1 --targets "$TARGETS" --judge-version jv10 \
        --judge-model z-ai/glm-5.2 --pin-provider DeepInfra --workers "$JW" --max-tokens 16000 \
        --selection-label "glm batch2: askA/askG +15, ask_r12_1/2 +19, seeds 2000-2399" \
        --replicate "$R" $extra &
    pids+=($!)
  done
  local pid; for pid in "${pids[@]}"; do wait "$pid"; done
}

idle=0
while :; do
  build_targets
  n_roll=$(ls experiments/agent1/outputs/v15/inf_ask*_glm_s2[0-3]??.json 2>/dev/null | grep -cE "_s[0-9]+\.json$")
  n_verd=$(ls experiments/agent1/outputs/v15/inf_ask*_glm_s2[0-3]??.deception_jv10_zaiglm52*.json 2>/dev/null | wc -l)
  echo "[$(date +%H:%M:%S)] rollouts=$n_roll/$WANT_ROLLOUTS verdicts=$n_verd/$(( WANT_ROLLOUTS * 2 ))"
  [ -s "$TARGETS" ] && sweep
  n_verd=$(ls experiments/agent1/outputs/v15/inf_ask*_glm_s2[0-3]??.deception_jv10_zaiglm52*.json 2>/dev/null | wc -l)
  if [ "$n_roll" -ge "$WANT_ROLLOUTS" ] && [ "$n_verd" -ge $(( WANT_ROLLOUTS * 2 )) ]; then
    echo "[$(date +%H:%M:%S)] all rollouts judged"; break
  fi
  if [ "$n_verd" -ge $(( WANT_ROLLOUTS * 2 )) ]; then idle=$(( idle + 1 )); else idle=0; fi
  [ "$idle" -ge "$IDLE_LIMIT" ] && { echo "idle limit reached"; break; }
  sleep 60
done
sweep --repair
echo "=== tally ==="
for arm in askA askG ask_r12_1 ask_r12_2; do
  n=$(ls experiments/agent1/outputs/v15/inf_${arm}_glm_s2[0-3]??.json 2>/dev/null | grep -cE "_s[0-9]+\.json$")
  v=$(ls experiments/agent1/outputs/v15/inf_${arm}_glm_s2[0-3]??.deception_jv10_zaiglm52*.json 2>/dev/null | wc -l)
  echo "  $arm: $n rollouts, $v verdict files"
done
echo "[$(date +%H:%M:%S)] GLM BATCH2 JUDGE FINISHED"
