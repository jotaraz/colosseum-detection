#!/bin/bash
# 10 more askG seeds per model on v15: deepseek-v4-flash-0731, kimi-k2.6, glm-5.2 = 30 runs.
# Takes each v15 askG cell from 5 seeds to 15.
#
# Runs on a COMPUTE node — OpenRouter via the execute node's HTTPS proxy, not the login node.
#
#   condor_submit_bid 100 cluster/run_agent1_v15_askG_more.sub
#
# NB the three cells are not configured identically, by history rather than by design:
#   deepseek  max_tokens=16000, pinned GMICloud   (changed 2026-08-21, see any deepseek config)
#   kimi      max_tokens=8000,  unpinned
#   glm       max_tokens=8000,  pinned [DeepInfra, Fireworks]  (pre-existing)
# So these 10 deepseek runs match seeds 236/284 but NOT 235/283/285, which are still 8k
# unpinned. That cell ends up 3 old + 12 new.
set -uo pipefail
export HOME="${HOME:-/home/jtaraz}"
export PYTHONUNBUFFERED=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"
export PYTHONPATH="$PROJECT"
set -a; source "$PROJECT/.env"; set +a
[ -n "${OPENROUTER_API_KEY:-}" ] || { echo "FATAL: OPENROUTER_API_KEY unset" >&2; exit 1; }
mkdir -p "$PROJECT/logs"
echo "[$(date +%H:%M:%S)] host=$(hostname) proxy=${https_proxy:-<none>}"

JOBS=""
seed=319
for model in deepseek kimi glm; do
  for i in $(seq 1 10); do JOBS="${JOBS}${model} ${seed}"$'\n'; seed=$((seed+1)); done
done

run_one() {
  read -r model seed <<< "$1"
  cfg="experiments/agent1/configs/agent1_v15_inf_askG_${model}.yaml"
  out="experiments/agent1/outputs/v15/inf_askG_${model}_s${seed}.json"
  [ -f "$out" ] && { echo "[$(date +%H:%M:%S)] SKIP  $out"; return 0; }
  echo "[$(date +%H:%M:%S)] START v15 askG $model s$seed"
  "$PROJECT/.venv/bin/python" -m experiments.agent1.run \
      --config "$cfg" --seed "$seed" --out "$out" \
      > "$PROJECT/logs/v15G_${model}_s${seed}.log" 2>&1
  rc=$?
  if [ -f "$out" ]; then echo "[$(date +%H:%M:%S)] DONE  $model s$seed rc=$rc"
  else echo "[$(date +%H:%M:%S)] FAIL  $model s$seed rc=$rc — no record"; fi
}
export -f run_one; export PROJECT

echo "$JOBS" | grep -v '^$' | xargs -P 6 -I{} bash -c 'run_one "$@"' _ {}
echo "[$(date +%H:%M:%S)] V15 ASKG MORE BATCH FINISHED"

echo "=== summary ==="
"$PROJECT/.venv/bin/python" - <<'PY'
import json, glob, collections
rows=[]
for p in sorted(glob.glob("experiments/agent1/outputs/v15/inf_askG_*_s3*.json")):
    if ".category2_" in p: continue
    s=int(p.split("_s")[-1][:-5])
    if not (319 <= s <= 348): continue
    r=json.load(open(p))
    provs={st.get("provider") for t in r["turns"] for st in (t.get("steps_detail") or [])}
    rows.append((s, p.split("/")[-1], r["outcome"], r["elapsed_seconds"]/60, r["cost"],
                 r["discards"]["unsalvaged"], sorted(x for x in provs if x)))
print("  %d/30 records" % len(rows))
print("  outcomes:", dict(collections.Counter(x[2] for x in rows)))
print("  with an ended turn:", [x[1] for x in rows if x[5]] or "none")
print("  total $%.2f" % sum(x[4] for x in rows))
for s,n,o,m,c,u,pr in rows:
    print("   %-32s %-10s %5.1fmin $%.2f unsalv=%d %s" % (n,o,m,c,u,pr))
PY
