#!/bin/bash
# v18 rollouts: askA and askG x 3 models x 2 seeds = 12 runs.
#
# v18 is v15 with the grievance moved off Marcus and onto Tomas — six messages differ and
# nothing else, so v15/v18 is a minimal pair over WHO is being refused. The ask arms name the
# avoided colleague, so on v18 they name Tomas (make_ask_configs.FIXTURES["v18"]); an arm
# still naming Marcus would tell Nadia's assistant to avoid the one colleague she has no
# problem with. Read against the existing v15 askA/askG cells.
#
# Runs on a COMPUTE node — OpenRouter via the execute node's HTTPS proxy, not the login node.
#
#   condor_submit_bid 100 cluster/run_agent1_v18_askAG.sub
#
# Cell configuration, inherited from the v15 configs these were generated from:
#   deepseek  max_tokens=16000, pinned GMICloud (allow_fallbacks=false)
#   kimi      max_tokens=8000,  unpinned
#   glm       max_tokens=8000,  order [DeepInfra, Fireworks]
# Seeds 400-411 — the corpus high-water mark was 396, so nothing collides.
set -uo pipefail
export HOME="${HOME:-/home/jtaraz}"
export PYTHONUNBUFFERED=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"
export PYTHONPATH="$PROJECT"
set -a; source "$PROJECT/.env"; set +a
[ -n "${OPENROUTER_API_KEY:-}" ] || { echo "FATAL: OPENROUTER_API_KEY unset" >&2; exit 1; }
mkdir -p "$PROJECT/logs" "$PROJECT/experiments/agent1/outputs/v18"
echo "[$(date +%H:%M:%S)] host=$(hostname) proxy=${https_proxy:-<none>}"

# Fail before spending anything if the world or a config is missing.
[ -f "$PROJECT/experiments/agent1/fixtures/aug2026_v18_renamed.json" ] \
  || { echo "FATAL: v18 fixture not deployed" >&2; exit 1; }

JOBS=""
seed=400
for arm in askA askG; do
  for model in deepseek kimi glm; do
    for i in 1 2; do JOBS="${JOBS}${arm} ${model} ${seed}"$'\n'; seed=$((seed+1)); done
  done
done

run_one() {
  read -r arm model seed <<< "$1"
  cfg="experiments/agent1/configs/agent1_v18_inf_${arm}_${model}.yaml"
  out="experiments/agent1/outputs/v18/inf_${arm}_${model}_s${seed}.json"
  [ -f "$cfg" ] || { echo "[$(date +%H:%M:%S)] FATAL no config $cfg"; return 1; }
  [ -f "$out" ] && { echo "[$(date +%H:%M:%S)] SKIP  $out"; return 0; }
  echo "[$(date +%H:%M:%S)] START v18 $arm $model s$seed"
  "$PROJECT/.venv/bin/python" -m experiments.agent1.run \
      --config "$cfg" --seed "$seed" --out "$out" \
      > "$PROJECT/logs/v18_${arm}_${model}_s${seed}.log" 2>&1
  rc=$?
  if [ -f "$out" ]; then echo "[$(date +%H:%M:%S)] DONE  $arm $model s$seed rc=$rc"
  else echo "[$(date +%H:%M:%S)] FAIL  $arm $model s$seed rc=$rc — no record"; fi
}
export -f run_one; export PROJECT

echo "$JOBS" | grep -v '^$' | xargs -P 6 -I{} bash -c 'run_one "$@"' _ {}
echo "[$(date +%H:%M:%S)] V18 ASKA/ASKG BATCH FINISHED"

echo "=== summary ==="
"$PROJECT/.venv/bin/python" - <<'PY'
import json, glob, collections
rows=[]
for p in sorted(glob.glob("experiments/agent1/outputs/v18/inf_ask*_s4*.json")):
    if ".category2_" in p: continue
    s=int(p.split("_s")[-1][:-5])
    if not (400 <= s <= 411): continue
    r=json.load(open(p))
    provs={st.get("provider") for t in r["turns"] for st in (t.get("steps_detail") or [])}
    rows.append((s, p.split("/")[-1], r["outcome"], r["elapsed_seconds"]/60, r["cost"],
                 r["discards"]["unsalvaged"], sorted(x for x in provs if x)))
print("  %d/12 records" % len(rows))
print("  outcomes:", dict(collections.Counter(x[2] for x in rows)))
print("  with an ended turn:", [x[1] for x in rows if x[5]] or "none")
print("  total $%.2f" % sum(x[4] for x in rows))
for s,n,o,m,c,u,pr in rows:
    print("   %-34s %-10s %5.1fmin $%.2f unsalv=%d %s" % (n,o,m,c,u,pr))
PY
