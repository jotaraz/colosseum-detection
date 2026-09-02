#!/bin/bash
# v15 name-set rollouts: one name set per job, 30 runs each.
#
#   30 = 2 arms (askA, askG) x 3 models (deepseek, kimi, glm) x 5 seeds
#
# The world is v15's, unchanged — same `version`, same `note`, same 301 messages — with the
# four principals renamed and the pronouns that follow them repaired (fixtures/VERSIONS.md,
# "Name sets"). So each cell against the matching v15 cell isolates the cast and nothing else:
#
#     b  Rajesh  Marcus  Nikolai  Tomas    m m m m   the data scientists made male
#     c  Priya   Martha  Nadia    Tessa    f f f f   the other two made female
#     d  Rajesh  Martha  Nikolai  Tessa    m f m f   both pairs crossed
#
# Rollouts only — no judges. jv10 is Priya-only by construction (its goal block names her and
# uses she/her throughout, and lie_over_agent1 refuses a target list naming anyone else), so
# judging sets b and d needs that prompt parameterized first. Deliberately not done here.
#
# Runs on a COMPUTE node — OpenRouter via the execute node's HTTPS proxy, not the login node.
#
#   condor_submit_bid 100 cluster/run_agent1_v15_namesets.sub
#
# -P 6 is the proven per-node concurrency from run_agent1_v15_askG_more.sh, kept rather than
# raised; the three sets run as three jobs instead, so throughput comes from nodes and a
# failure is contained to one set.
#
# NB the three model cells are not configured identically, by history rather than by design —
# this is inherited from the v15 configs these derive from, and is the same asymmetry the
# v15 baseline carries:
#   deepseek  max_tokens=16000, pinned GMICloud
#   kimi      max_tokens=8000,  unpinned
#   glm       max_tokens=8000,  pinned [DeepInfra, Fireworks]
set -uo pipefail
NS="${1:?usage: run_agent1_v15_namesets.sh <nb|nc|nd>}"
export HOME="${HOME:-/home/jtaraz}"
export PYTHONUNBUFFERED=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"
export PYTHONPATH="$PROJECT"
set -a; source "$PROJECT/.env"; set +a
[ -n "${OPENROUTER_API_KEY:-}" ] || { echo "FATAL: OPENROUTER_API_KEY unset" >&2; exit 1; }
mkdir -p "$PROJECT/logs" "$PROJECT/experiments/agent1/outputs/v15${NS}"
echo "[$(date +%H:%M:%S)] host=$(hostname) set=$NS proxy=${https_proxy:-<none>}"

# Seeds are globally unique, as every earlier batch: 501-530 nb, 531-560 nc, 561-590 nd.
case "$NS" in
  nb) seed=501 ;;
  nc) seed=531 ;;
  nd) seed=561 ;;
  *)  echo "FATAL: unknown name set '$NS' (want nb|nc|nd)" >&2; exit 1 ;;
esac

JOBS=""
for arm in askA askG; do
  for model in deepseek kimi glm; do
    for i in $(seq 1 5); do JOBS="${JOBS}${arm} ${model} ${seed}"$'\n'; seed=$((seed+1)); done
  done
done

run_one() {
  read -r arm model seed <<< "$1"
  cfg="experiments/agent1/configs/agent1_v15${NS}_inf_${arm}_${model}.yaml"
  out="experiments/agent1/outputs/v15${NS}/inf_${arm}_${model}_s${seed}.json"
  [ -f "$cfg" ] || { echo "[$(date +%H:%M:%S)] MISSING $cfg"; return 1; }
  [ -f "$out" ] && { echo "[$(date +%H:%M:%S)] SKIP  $out"; return 0; }
  echo "[$(date +%H:%M:%S)] START v15${NS} $arm $model s$seed"
  "$PROJECT/.venv/bin/python" -m experiments.agent1.run \
      --config "$cfg" --seed "$seed" --out "$out" \
      > "$PROJECT/logs/v15${NS}_${arm}_${model}_s${seed}.log" 2>&1
  rc=$?
  if [ -f "$out" ]; then echo "[$(date +%H:%M:%S)] DONE  $arm $model s$seed rc=$rc"
  else echo "[$(date +%H:%M:%S)] FAIL  $arm $model s$seed rc=$rc — no record"; fi
}
export -f run_one; export PROJECT NS

echo "$JOBS" | grep -v '^$' | xargs -P 6 -I{} bash -c 'run_one "$@"' _ {}
echo "[$(date +%H:%M:%S)] V15 NAMESET $NS BATCH FINISHED"

echo "=== summary ==="
NS="$NS" "$PROJECT/.venv/bin/python" - <<'PY'
import json, glob, os, collections
ns = os.environ["NS"]
rows = []
for p in sorted(glob.glob(f"experiments/agent1/outputs/v15{ns}/inf_ask*_s*.json")):
    if ".category2_" in p or ".metrics" in p: continue
    r = json.load(open(p))
    provs = {st.get("provider") for t in r["turns"] for st in (t.get("steps_detail") or [])}
    rows.append((p.split("/")[-1], r["outcome"], r["elapsed_seconds"] / 60, r["cost"],
                 r["discards"]["unsalvaged"], sorted(x for x in provs if x)))
print("  %d/30 records" % len(rows))
print("  outcomes:", dict(collections.Counter(x[1] for x in rows)))
print("  with an ended turn:", [x[0] for x in rows if x[4]] or "none")
print("  total $%.2f" % sum(x[3] for x in rows))
for n, o, m, c, u, pr in rows:
    print("   %-34s %-10s %5.1fmin $%.2f unsalv=%d %s" % (n, o, m, c, u, pr))
PY
