#!/bin/bash
# v15 x {kimi-k2.6, deepseek-v4-flash-0731, glm-5.2} x 2 seeds x 8 arms = 48 runs.
# Arms: K L M P1 P2 P3 P4 P5 — the pressure-on-Priya series. Seven override Nadia+Tomas
# (Nadia's half is askA or askG verbatim); askP2 is single-principal, Nadia only.
#
#   condor_submit_bid 100 cluster/run_agent1_v15_pseries.sub
set -uo pipefail
export HOME="${HOME:-/home/jtaraz}"; export PYTHONUNBUFFERED=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"; export PYTHONPATH="$PROJECT"
set -a; source "$PROJECT/.env"; set +a
[ -n "${OPENROUTER_API_KEY:-}" ] || { echo "FATAL: OPENROUTER_API_KEY unset" >&2; exit 1; }
mkdir -p "$PROJECT/logs"
echo "[$(date +%F\ %H:%M:%S)] p-series on $(hostname), proxy=${https_proxy:-<none>}"

JOBS=""; seed=349
for arm in askK askL askM askP1 askP2 askP3 askP4 askP5; do
  for model in deepseek kimi glm; do
    for i in 1 2; do JOBS="${JOBS}${arm} ${model} ${seed}"$'\n'; seed=$((seed+1)); done
  done
done
echo "$JOBS" | grep -c . | sed 's/^/  jobs queued: /'

run_one() {
  read -r arm model seed <<< "$1"
  cfg="experiments/agent1/configs/agent1_v15_inf_${arm}_${model}.yaml"
  out="experiments/agent1/outputs/v15/inf_${arm}_${model}_s${seed}.json"
  [ -f "$cfg" ] || { echo "[$(date +%H:%M:%S)] FATAL no config $cfg"; return 1; }
  [ -f "$out" ] && { echo "[$(date +%H:%M:%S)] SKIP $out"; return 0; }
  echo "[$(date +%H:%M:%S)] START $arm $model s$seed"
  "$PROJECT/.venv/bin/python" -m experiments.agent1.run \
      --config "$cfg" --seed "$seed" --out "$out" \
      > "$PROJECT/logs/pser_${arm}_${model}_s${seed}.log" 2>&1
  rc=$?
  [ -f "$out" ] && echo "[$(date +%H:%M:%S)] DONE  $arm $model s$seed rc=$rc" \
                || echo "[$(date +%H:%M:%S)] FAIL  $arm $model s$seed rc=$rc — no record"
}
export -f run_one; export PROJECT
echo "$JOBS" | grep -v '^$' | xargs -P 6 -I{} bash -c 'run_one "$@"' _ {}
echo "[$(date +%F\ %H:%M:%S)] P-SERIES ROLLOUTS FINISHED"

"$PROJECT/.venv/bin/python" - <<'PY'
import json, glob, collections
rows=[]
for p in sorted(glob.glob("experiments/agent1/outputs/v15/inf_ask*_s3*.json")):
    if ".category2_" in p: continue
    s=int(p.split("_s")[-1][:-5])
    if not (349 <= s <= 396): continue
    r=json.load(open(p)); rows.append((p.split("/")[-1], r))
print("  records: %d / 48" % len(rows))
print("  outcomes:", dict(collections.Counter(r["outcome"] for _,r in rows)))
print("  with an ended turn:", [n for n,r in rows if r["discards"]["unsalvaged"]] or "none")
print("  total $%.2f" % sum(r["cost"] for _,r in rows))
by=collections.Counter(n.split("_")[1] for n,_ in rows)
print("  per arm:", dict(by))
PY
