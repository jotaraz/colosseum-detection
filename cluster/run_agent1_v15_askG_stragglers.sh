#!/bin/bash
# The 3 remaining v15 askG deepseek runs still at max_tokens=8000, unpinned: seeds 235, 283, 285.
# Overwritten in place at 16000 + GMICloud, so the whole v15 askG deepseek cell (15 seeds)
# is configured identically instead of 3 old + 12 new.
set -uo pipefail
export HOME="${HOME:-/home/jtaraz}"; export PYTHONUNBUFFERED=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"; export PYTHONPATH="$PROJECT"
set -a; source "$PROJECT/.env"; set +a
[ -n "${OPENROUTER_API_KEY:-}" ] || { echo "FATAL: OPENROUTER_API_KEY unset" >&2; exit 1; }
mkdir -p "$PROJECT/logs"
echo "[$(date +%H:%M:%S)] host=$(hostname)"
run_one() {
  seed="$1"
  out="experiments/agent1/outputs/v15/inf_askG_deepseek_s${seed}.json"
  rm -f "$out" "${out%.json}.html"
  echo "[$(date +%H:%M:%S)] START v15 askG deepseek s$seed"
  "$PROJECT/.venv/bin/python" -m experiments.agent1.run \
      --config experiments/agent1/configs/agent1_v15_inf_askG_deepseek.yaml \
      --seed "$seed" --out "$out" > "$PROJECT/logs/strag_s${seed}.log" 2>&1
  rc=$?
  [ -f "$out" ] && echo "[$(date +%H:%M:%S)] DONE s$seed rc=$rc" || echo "[$(date +%H:%M:%S)] FAIL s$seed rc=$rc"
}
export -f run_one; export PROJECT
printf '%s\n' 235 283 285 | xargs -P 3 -I{} bash -c 'run_one "$@"' _ {}
echo "[$(date +%H:%M:%S)] STRAGGLERS FINISHED"
"$PROJECT/.venv/bin/python" - <<'PY'
import json, glob
for s in ("235","283","285"):
    p=f"experiments/agent1/outputs/v15/inf_askG_deepseek_s{s}.json"
    r=json.load(open(p)); gp=r["config"]["generation_params"]
    provs={st.get("provider") for t in r["turns"] for st in (t.get("steps_detail") or [])}
    print("  s%s %-10s unsalv=%d mt=%s served_by=%s $%.3f" % (
        s, r["outcome"], r["discards"]["unsalvaged"], gp.get("max_tokens"),
        sorted(x for x in provs if x), r["cost"]))
PY
