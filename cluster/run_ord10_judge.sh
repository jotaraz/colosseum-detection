#!/bin/bash
# jv7 x3 over the 10 remaining v17 ord* rollouts (ordNMPR s320/321/323/324 + ordRPMN s325-330),
# on a compute node; same recipe as run_ord_judge.sh (probed GMICloud pin, PM+DS roles).
export HOME="${HOME:-/home/jtaraz}"
set -euo pipefail
export PYTHONUNBUFFERED=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"
set -a; source "$PROJECT/.env"; set +a
[ -n "${OPENROUTER_API_KEY:-}" ] || { echo "FATAL: OPENROUTER_API_KEY unset" >&2; exit 1; }
echo "node: $(hostname) | proxy: ${https_proxy:-none}"
PIN_ARGS=(--pin-provider GMICloud)
if "$PROJECT/.venv/bin/python" - <<'PY'
import json, os, sys, urllib.request
body=json.dumps({"model":"deepseek/deepseek-v4-flash-0731",
    "messages":[{"role":"user","content":"Reply with the single word: ok"}],
    "max_completion_tokens":16,"provider":{"order":["GMICloud"],"allow_fallbacks":False}}).encode()
req=urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body,
    headers={"Content-Type":"application/json","Authorization":f"Bearer {os.environ['OPENROUTER_API_KEY']}"})
try:
    with urllib.request.urlopen(req, timeout=90) as r: d=json.loads(r.read())
    ok = not d.get("error") and (d.get("choices") or [{}])[0].get("message",{}).get("content")
    print(f"pin probe: provider={d.get('provider')} ok={bool(ok)}"); sys.exit(0 if ok else 1)
except Exception as e:
    print(f"pin probe FAILED: {type(e).__name__}: {e}"); sys.exit(1)
PY
then echo "pinning to GMICloud"; else echo "pin probe failed -> unpinned"; PIN_ARGS=(); fi
RUNS=(experiments/agent1/outputs/v17/inf_askI_ordNMPR_deepseek_s320.json
      experiments/agent1/outputs/v17/inf_askI_ordNMPR_kimi_s321.json
      experiments/agent1/outputs/v17/inf_askI_ordNMPR_glm_s323.json
      experiments/agent1/outputs/v17/inf_askI_ordNMPR_glm_s324.json
      experiments/agent1/outputs/v17/inf_askI3_ordRPMN_deepseek_s325.json
      experiments/agent1/outputs/v17/inf_askI3_ordRPMN_deepseek_s326.json
      experiments/agent1/outputs/v17/inf_askI3_ordRPMN_kimi_s327.json
      experiments/agent1/outputs/v17/inf_askI3_ordRPMN_kimi_s328.json
      experiments/agent1/outputs/v17/inf_askI3_ordRPMN_glm_s329.json
      experiments/agent1/outputs/v17/inf_askI3_ordRPMN_glm_s330.json)
rc=0; i=0; pids=()
for f in "${RUNS[@]}"; do
  for r in 1 2 3; do
    tag="$(basename "$f" .json)_r${r}"
    "$PROJECT/.venv/bin/python" -m experiments.agent2.category2_over_agent1 "$f" \
      --provider openrouter --judge-model deepseek/deepseek-v4-flash-0731 \
      --workers 3 --baseline 0 --replicate "$r" \
      --roles "Data Scientist,Product Manager" "${PIN_ARGS[@]}" \
      > "$PROJECT/cluster/ord10_${tag}.log" 2>&1 &
    pids+=($!); i=$((i+1))
    if (( i % 8 == 0 )); then for p in "${pids[@]}"; do wait "$p" || rc=1; done; pids=(); fi
  done
done
for p in "${pids[@]}"; do wait "$p" || rc=1; done
echo "--- verdicts written:"
for f in "${RUNS[@]}"; do ls "${f%.json}".category2_jv7* 2>/dev/null; done | wc -l
exit $rc
