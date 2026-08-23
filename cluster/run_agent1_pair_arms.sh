#!/bin/bash
# The pair arms — askJ-askN and askP1-askP5 — on v15 and/or v16, over deepseek/kimi/glm.
#
# Runs on a COMPUTE node — OpenRouter via the execute node's HTTPS proxy, not the login node.
#
#   condor_submit_bid 100 cluster/run_agent1_pair_arms.sub
#
# Everything is env-driven, so one script covers a pilot and a full batch. Defaults: v16,
# all ten arms, three models, 2 seeds each = 60 runs.
#
#   FIXTURE="v15 v16"  which worlds (v15 keeps the incidental shop talk, v16 removed it)
#   ARMS="askP1 askP3" which arms; must be names from the canonical list below
#   MODELS="deepseek"  which cells
#   SEEDS=5            seeds per cell
#
# **Seed allocation is deterministic per cell, not sequential over the job list.** Each
# (fixture, arm, model) owns a block of 10 seeds computed from its position in the canonical
# lists, so running a subset today and the rest tomorrow cannot collide, and a cell can be
# extended from 2 seeds to 10 by raising SEEDS alone. Blocks: v15 400-699, v16 700-999.
# Nothing on disk uses a seed above 348.
#
# NB the three cells are not configured identically, by history rather than by design:
#   deepseek  max_tokens=16000, pinned GMICloud
#   kimi      max_tokens=8000,  unpinned
#   glm       max_tokens=8000,  pinned [DeepInfra, Fireworks]
# The arms inherit whatever their `_both_` baseline carries, so this matches askA/askG.
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

# Canonical order — the seed block depends on it, so DO NOT reorder these two lists.
ALL_ARMS=(askJ askK askL askM askN askP1 askP2 askP3 askP4 askP5)
ALL_MODELS=(deepseek kimi glm)

FIXTURE="${FIXTURE:-v16}"
ARMS="${ARMS:-${ALL_ARMS[*]}}"
MODELS="${MODELS:-${ALL_MODELS[*]}}"
SEEDS="${SEEDS:-2}"
(( SEEDS <= 10 )) || { echo "FATAL: SEEDS>10 would run into the next cell's block" >&2; exit 1; }

index_of() {  # $1 = needle, $2... = haystack; prints the position or -1
  local needle="$1"; shift; local i=0
  for x in "$@"; do [ "$x" = "$needle" ] && { echo "$i"; return; }; i=$((i+1)); done
  echo -1
}

JOBS=""
for fixture in $FIXTURE; do
  case "$fixture" in
    v15) base=400 ;;
    v16) base=700 ;;
    *) echo "FATAL: no seed block reserved for $fixture" >&2; exit 1 ;;
  esac
  for arm in $ARMS; do
    ai=$(index_of "$arm" "${ALL_ARMS[@]}")
    [ "$ai" -ge 0 ] || { echo "FATAL: unknown arm $arm" >&2; exit 1; }
    for model in $MODELS; do
      mi=$(index_of "$model" "${ALL_MODELS[@]}")
      [ "$mi" -ge 0 ] || { echo "FATAL: unknown model $model" >&2; exit 1; }
      cell=$(( ai * ${#ALL_MODELS[@]} + mi ))          # 0..29
      for i in $(seq 0 $((SEEDS-1))); do
        JOBS="${JOBS}${fixture} ${arm} ${model} $(( base + cell * 10 + i ))"$'\n'
      done
    done
  done
done
echo "[$(date +%H:%M:%S)] $(echo "$JOBS" | grep -cv '^$') runs queued"

run_one() {
  read -r fixture arm model seed <<< "$1"
  cfg="experiments/agent1/configs/agent1_${fixture}_inf_${arm}_${model}.yaml"
  out="experiments/agent1/outputs/${fixture}/inf_${arm}_${model}_s${seed}.json"
  [ -f "$cfg" ] || { echo "[$(date +%H:%M:%S)] NOCFG $cfg"; return 0; }
  [ -f "$out" ] && { echo "[$(date +%H:%M:%S)] SKIP  $out"; return 0; }
  echo "[$(date +%H:%M:%S)] START $fixture $arm $model s$seed"
  "$PROJECT/.venv/bin/python" -m experiments.agent1.run \
      --config "$cfg" --seed "$seed" --out "$out" \
      > "$PROJECT/logs/${fixture}_${arm}_${model}_s${seed}.log" 2>&1
  rc=$?
  if [ -f "$out" ]; then echo "[$(date +%H:%M:%S)] DONE  $fixture $arm $model s$seed rc=$rc"
  else echo "[$(date +%H:%M:%S)] FAIL  $fixture $arm $model s$seed rc=$rc — no record"; fi
}
export -f run_one; export PROJECT

echo "$JOBS" | grep -v '^$' | xargs -P "${PAR:-6}" -I{} bash -c 'run_one "$@"' _ {}
echo "[$(date +%H:%M:%S)] PAIR ARMS BATCH FINISHED"

echo "=== summary ==="
FIXTURE="$FIXTURE" ARMS="$ARMS" MODELS="$MODELS" SEEDS="$SEEDS" \
"$PROJECT/.venv/bin/python" - <<'PY'
import json, os, glob, collections
arms = os.environ["ARMS"].split()
rows = []
for fixture in os.environ["FIXTURE"].split():
    for p in sorted(glob.glob(f"experiments/agent1/outputs/{fixture}/inf_*_s[0-9]*.json")):
        if ".category" in p or ".agent3" in p:
            continue
        name = p.split("/")[-1]
        arm = name.split("_")[1]
        seed = int(name.split("_s")[-1][:-5])
        if arm not in arms or not (400 <= seed <= 999):
            continue
        r = json.load(open(p))
        rows.append((fixture, name, r["outcome"], r["elapsed_seconds"] / 60, r["cost"],
                     r["discards"]["unsalvaged"]))
print("  %d records" % len(rows))
print("  outcomes:", dict(collections.Counter(x[2] for x in rows)))
print("  with an ended turn:", [x[1] for x in rows if x[5]] or "none")
print("  total $%.2f" % sum(x[4] for x in rows))
for f, n, o, m, c, u in rows:
    print("   %-40s %-10s %5.1fmin $%.2f unsalv=%d" % (n, o, m, c, u))
PY
