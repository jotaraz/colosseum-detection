#!/bin/bash
# 10 more askA seeds per model on v15: deepseek-v4-flash-0731, kimi-k2.6, glm-5.2 = 30 runs.
# Takes each v15 askA cell from 5 seeds to 15, matching what askG already has (15/15/15).
# ROLLOUTS ONLY — no judging. Seeds 412-441 (397-411 are taken: v18 occupies 400-411).
#
# Runs on a COMPUTE node — OpenRouter via the execute node's HTTPS proxy, not the login node.
#
#   condor_submit_bid 100 cluster/run_agent1_v15_askA_more.sub
#
# NB the three cells are not configured identically, by history rather than by design — same
# situation run_agent1_v15_askG_more.sh documents:
#   deepseek  max_tokens=16000, pinned GMICloud   (the 2026-08-21 truncation fix)
#   kimi      max_tokens=8000,  unpinned
#   glm       max_tokens=8000,  pinned [DeepInfra, Fireworks]
# So these 10 deepseek runs match seed 232 but NOT 231/301/302/303, which are still 8k
# unpinned: that cell ends up 4 old + 11 new. Chosen deliberately (2026-08-24) over re-running
# the old four, because askG deepseek carries the same 3-old/12-new split, so askA and askG
# stay comparable to each other. kimi and glm cells stay internally uniform at 15/15.
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
seed=412
for model in deepseek kimi glm; do
  for i in $(seq 1 10); do JOBS="${JOBS}${model} ${seed}"$'\n'; seed=$((seed+1)); done
done

run_one() {
  read -r model seed <<< "$1"
  cfg="experiments/agent1/configs/agent1_v15_inf_askA_${model}.yaml"
  out="experiments/agent1/outputs/v15/inf_askA_${model}_s${seed}.json"
  [ -f "$out" ] && { echo "[$(date +%H:%M:%S)] SKIP  $out"; return 0; }
  echo "[$(date +%H:%M:%S)] START v15 askA $model s$seed"
  "$PROJECT/.venv/bin/python" -m experiments.agent1.run \
      --config "$cfg" --seed "$seed" --out "$out" \
      > "$PROJECT/logs/v15A_${model}_s${seed}.log" 2>&1
  rc=$?
  if [ -f "$out" ]; then echo "[$(date +%H:%M:%S)] DONE  $model s$seed rc=$rc"
  else echo "[$(date +%H:%M:%S)] FAIL  $model s$seed rc=$rc — no record"; fi
}
export -f run_one; export PROJECT

echo "$JOBS" | grep -v '^$' | xargs -P 6 -I{} bash -c 'run_one "$@"' _ {}
echo "[$(date +%H:%M:%S)] V15 ASKA MORE BATCH FINISHED"

echo "=== summary ==="
"$PROJECT/.venv/bin/python" - <<'PY'
import json, glob, collections, re
rows=[]
for p in sorted(glob.glob("experiments/agent1/outputs/v15/inf_askA_*_s*.json")):
    m=re.search(r"_s(\d+)\.json$", p)
    if not m or ".category2_" in p or ".lie_" in p: continue
    s=int(m.group(1))
    if not (412 <= s <= 441): continue
    r=json.load(open(p))
    provs={st.get("provider") for t in r["turns"] for st in (t.get("steps_detail") or [])}
    rows.append((s, p.split("/")[-1], r["outcome"], r["elapsed_seconds"]/60, r["cost"],
                 r["discards"]["unsalvaged"], sorted(x for x in provs if x)))
print("  %d/30 records" % len(rows))
print("  outcomes:", dict(collections.Counter(x[2] for x in rows)))
print("  with an ended turn:", [x[1] for x in rows if x[5]] or "none")
print("  total $%.2f" % sum(x[4] for x in rows))
for s,n,o,mn,c,u,pr in rows:
    print("   %-32s %-10s %5.1fmin $%.2f unsalv=%d %s" % (n,o,mn,c,u,pr))
PY
