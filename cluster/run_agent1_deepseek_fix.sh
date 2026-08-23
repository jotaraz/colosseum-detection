#!/bin/bash
# Re-run the 7 agent1 deepseek-v4-flash-0731 runs that lost a turn to the 8000-token cap.
#
# Each of these ended at least one turn on `finish_reason=length` rather than because the
# model finished, which `agent.py` treats as not-evidence about the model. The configs now
# carry max_tokens=16000 and a GMICloud pin (see the note in any deepseek config for why
# both halves are needed). Records are OVERWRITTEN in place, by request — same seeds, same
# filenames — so the old 8k records do not survive.
#
# Runs on a COMPUTE node: OpenRouter is reached through the execute node's HTTPS proxy, not
# from the login node and not from a laptop.
#
#   condor_submit_bid 100 cluster/run_agent1_deepseek_fix.sub
set -uo pipefail
export HOME="${HOME:-/home/jtaraz}"
export PYTHONUNBUFFERED=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"
export PYTHONPATH="$PROJECT"

set -a; source "$PROJECT/.env"; set +a
[ -n "${OPENROUTER_API_KEY:-}" ] || { echo "FATAL: OPENROUTER_API_KEY unset" >&2; exit 1; }

echo "[$(date +%H:%M:%S)] host=$(hostname) proxy=${https_proxy:-<none>}"

# fixture · arm · seed — the 7 defective runs, from the discards audit.
JOBS="
v15 askA 232
v15 askG 236
v15 askG 284
v16 askA 239
v16 askG 243
v16 askG 293
v16 askG2 262
"

run_one() {
  read -r v arm seed <<< "$1"
  cfg="experiments/agent1/configs/agent1_${v}_inf_${arm}_deepseek.yaml"
  out="experiments/agent1/outputs/${v}/inf_${arm}_deepseek_s${seed}.json"
  [ -f "$cfg" ] || { echo "[$(date +%H:%M:%S)] FATAL no config $cfg"; return 1; }
  # Overwrite: the runner has no skip guard of its own, but a stale record would otherwise
  # sit beside the new one if the run died, so clear both record and viewer up front.
  rm -f "$out" "${out%.json}.html"
  echo "[$(date +%H:%M:%S)] START $v $arm s$seed"
  "$PROJECT/.venv/bin/python" -m experiments.agent1.run \
      --config "$cfg" --seed "$seed" --out "$out" \
      > "$PROJECT/logs/dsfix_${v}_${arm}_s${seed}.log" 2>&1
  rc=$?
  if [ -f "$out" ]; then echo "[$(date +%H:%M:%S)] DONE  $v $arm s$seed rc=$rc"
  else echo "[$(date +%H:%M:%S)] FAIL  $v $arm s$seed rc=$rc — no record"; fi
}
export -f run_one; export PROJECT
mkdir -p "$PROJECT/logs"

# 3 at a time: each run is one in-flight request, so this is 3 concurrent calls.
echo "$JOBS" | grep -v '^$' | xargs -P 3 -I{} bash -c 'run_one "$@"' _ {}

echo "[$(date +%H:%M:%S)] DEEPSEEK FIX BATCH FINISHED"
echo "=== resulting discards ==="
"$PROJECT/.venv/bin/python" - <<'PY'
import json, glob
for v in ("v15","v16"):
    for p in sorted(glob.glob(f"experiments/agent1/outputs/{v}/inf_ask*_deepseek_s*.json")):
        s=p.split("_s")[-1][:-5]
        if s not in {"232","236","284","239","243","293","262"}: continue
        r=json.load(open(p)); d=r["discards"]
        provs=set()
        for t in r["turns"]:
            for st in (t.get("steps_detail") or []): provs.add(st.get("provider"))
        print(f"  {p.split('/')[-1]:34} {r['outcome']:10} unsalv={d['unsalvaged']} trunc={d['by_verdict'].get('truncated',0)} providers={sorted(x for x in provs if x)}")
PY
