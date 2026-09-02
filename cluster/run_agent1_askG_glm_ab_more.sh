#!/bin/bash
# 20 more askG/glm seeds on name set a and on name set b — 30 -> 50 each.
#
# One job per set so they run side by side at the proven -P 6.
#
#   condor_submit_bid 100 cluster/run_agent1_askG_glm_ab_more.sub
#
# Why: at 30 a side the cell stands at a 7/30 vs 14/30 convergence split, Fisher two-sided
# p = 0.10. 50 a side is the cheapest way to find out whether that is real. Same arm, same
# model, same config lineage — set b's config is DERIVED from set a's by make_namesets.py, so
# the two differ by the cast and nothing else (a: Priya/Marcus/Nadia/Tomas,
# b: Rajesh/Marcus/Nikolai/Tomas — the data scientists made male, the avoided colleague and
# the fourth seat left alone).
#
# Runs on a COMPUTE node — OpenRouter via the execute node's HTTPS proxy, not the login node.
#
# Seeds continue the block: 501-530 nb, 531-560 nc, 561-590 nd, 591-615 nb askG glm.
#   a: 616-635    b: 636-655
set -uo pipefail
CELL="${1:?usage: run_agent1_askG_glm_ab_more.sh <a|b>}"
export HOME="${HOME:-/home/jtaraz}"
export PYTHONUNBUFFERED=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"
export PYTHONPATH="$PROJECT"
set -a; source "$PROJECT/.env"; set +a
[ -n "${OPENROUTER_API_KEY:-}" ] || { echo "FATAL: OPENROUTER_API_KEY unset" >&2; exit 1; }

case "$CELL" in
  a) CFG="experiments/agent1/configs/agent1_v15_inf_askG_glm.yaml"
     DIR="experiments/agent1/outputs/v15";   FIRST=616 ;;
  b) CFG="experiments/agent1/configs/agent1_v15nb_inf_askG_glm.yaml"
     DIR="experiments/agent1/outputs/v15nb"; FIRST=636 ;;
  *) echo "FATAL: unknown cell '$CELL' (want a|b)" >&2; exit 1 ;;
esac
LAST=$((FIRST+19))
[ -f "$CFG" ] || { echo "FATAL: $CFG missing — rsync the configs first" >&2; exit 1; }
mkdir -p "$PROJECT/logs" "$PROJECT/$DIR"
echo "[$(date +%H:%M:%S)] host=$(hostname) cell=$CELL cfg=$CFG seeds=$FIRST-$LAST proxy=${https_proxy:-<none>}"

run_one() {
  seed="$1"
  out="${DIR}/inf_askG_glm_s${seed}.json"
  [ -f "$out" ] && { echo "[$(date +%H:%M:%S)] SKIP  $out"; return 0; }
  echo "[$(date +%H:%M:%S)] START cell $CELL askG glm s$seed"
  "$PROJECT/.venv/bin/python" -m experiments.agent1.run \
      --config "$CFG" --seed "$seed" --out "$out" \
      > "$PROJECT/logs/askGglm_${CELL}_s${seed}.log" 2>&1
  rc=$?
  if [ -f "$out" ]; then echo "[$(date +%H:%M:%S)] DONE  s$seed rc=$rc"
  else echo "[$(date +%H:%M:%S)] FAIL  s$seed rc=$rc — no record"; fi
}
export -f run_one; export PROJECT CFG DIR CELL

seq "$FIRST" "$LAST" | xargs -P 6 -I{} bash -c 'run_one "$@"' _ {}
echo "[$(date +%H:%M:%S)] ASKG GLM CELL $CELL MORE BATCH FINISHED"

echo "=== summary (whole cell, old + new) ==="
DIR="$DIR" "$PROJECT/.venv/bin/python" - <<'PY'
import json, glob, os, re, collections
d = os.environ["DIR"]
rows = []
for p in sorted(glob.glob(f"{d}/inf_askG_glm_s*.json")):
    if not re.search(r"inf_askG_glm_s\d+\.json$", p): continue
    r = json.load(open(p))
    rows.append((p.split("/")[-1], r["outcome"], r["elapsed_seconds"] / 60, r["cost"],
                 r["discards"]["unsalvaged"]))
c = collections.Counter(x[1] for x in rows)
print("  %d records (want 50)" % len(rows))
print("  outcomes:", dict(c))
print("  converged %d/%d = %.0f%%" % (c["converged"], len(rows),
                                      100 * c["converged"] / max(1, len(rows))))
print("  with an ended turn:", [x[0] for x in rows if x[4]] or "none")
print("  total $%.2f" % sum(x[3] for x in rows))
PY
