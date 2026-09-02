#!/bin/bash
# 25 more askG/glm seeds on name set b — takes that one cell from 5 to 30.
#
# Set b is v15 with the two data scientists made male (Alice->Rajesh, Carol->Nikolai) and
# Bob/Dan left alone, so the avoided colleague is still Marcus and the ask still says "him".
# See fixtures/VERSIONS.md, "Name sets".
#
# Why this cell: at 5 seeds it converged 4/5 against 7/30 for the same arm and model on set a,
# which is one or two runs' worth of difference and not readable. 30 makes it comparable to
# the v15 glm baseline, which is also 30.
#
# Runs on a COMPUTE node — OpenRouter via the execute node's HTTPS proxy, not the login node.
#
#   condor_submit_bid 100 cluster/run_agent1_v15nb_askG_glm_more.sub
#
# Seeds 591-615, continuing the block: 501-530 nb, 531-560 nc, 561-590 nd.
set -uo pipefail
export HOME="${HOME:-/home/jtaraz}"
export PYTHONUNBUFFERED=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"
export PYTHONPATH="$PROJECT"
set -a; source "$PROJECT/.env"; set +a
[ -n "${OPENROUTER_API_KEY:-}" ] || { echo "FATAL: OPENROUTER_API_KEY unset" >&2; exit 1; }
mkdir -p "$PROJECT/logs" "$PROJECT/experiments/agent1/outputs/v15nb"
echo "[$(date +%H:%M:%S)] host=$(hostname) proxy=${https_proxy:-<none>}"

CFG="experiments/agent1/configs/agent1_v15nb_inf_askG_glm.yaml"
[ -f "$CFG" ] || { echo "FATAL: $CFG missing — rsync the configs first" >&2; exit 1; }

run_one() {
  seed="$1"
  out="experiments/agent1/outputs/v15nb/inf_askG_glm_s${seed}.json"
  [ -f "$out" ] && { echo "[$(date +%H:%M:%S)] SKIP  $out"; return 0; }
  echo "[$(date +%H:%M:%S)] START v15nb askG glm s$seed"
  "$PROJECT/.venv/bin/python" -m experiments.agent1.run \
      --config "experiments/agent1/configs/agent1_v15nb_inf_askG_glm.yaml" \
      --seed "$seed" --out "$out" \
      > "$PROJECT/logs/v15nb_askG_glm_s${seed}.log" 2>&1
  rc=$?
  if [ -f "$out" ]; then echo "[$(date +%H:%M:%S)] DONE  s$seed rc=$rc"
  else echo "[$(date +%H:%M:%S)] FAIL  s$seed rc=$rc — no record"; fi
}
export -f run_one; export PROJECT

seq 591 615 | xargs -P 6 -I{} bash -c 'run_one "$@"' _ {}
echo "[$(date +%H:%M:%S)] V15NB ASKG GLM MORE BATCH FINISHED"

echo "=== summary (whole cell, old + new) ==="
"$PROJECT/.venv/bin/python" - <<'PY'
import json, glob, collections
rows = []
for p in sorted(glob.glob("experiments/agent1/outputs/v15nb/inf_askG_glm_s*.json")):
    if ".category2_" in p or ".metrics" in p: continue
    r = json.load(open(p))
    provs = {st.get("provider") for t in r["turns"] for st in (t.get("steps_detail") or [])}
    rows.append((p.split("/")[-1], r["outcome"], r["elapsed_seconds"] / 60, r["cost"],
                 r["discards"]["unsalvaged"], sorted(x for x in provs if x)))
print("  %d records (want 30)" % len(rows))
print("  outcomes:", dict(collections.Counter(x[1] for x in rows)))
print("  with an ended turn:", [x[0] for x in rows if x[4]] or "none")
print("  total $%.2f" % sum(x[3] for x in rows))
for n, o, m, c, u, pr in rows:
    print("   %-34s %-10s %5.1fmin $%.2f unsalv=%d %s" % (n, o, m, c, u, pr))
PY
