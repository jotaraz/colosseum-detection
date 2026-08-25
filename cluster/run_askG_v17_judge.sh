#!/bin/bash
# jv7 judging (CRITIC_CATEGORY6 + considered + absence verifier) of the four v17 askG rollouts
# that were never judged — the only gap in v17's jv7 coverage (72 verdict files over 28 rollouts
# = 24 judged x3, these 4 at zero). 3 replicates each = 12 runs, on a compute node so all
# OpenRouter traffic leaves via the node's HTTP(S) proxy, not a workstation.
#
# Settings match the 72 existing v17 verdicts exactly so these pool with them:
#   --roles "Data Scientist,Product Manager"  --baseline 0
#   deepseek/deepseek-v4-flash-0731 via openrouter, jv7
# (v17 seats Rafael as PM, so the default Data-Scientist-only roles would under-select stake.)
#
# Provider pinning: GMICloud, see experiments/agent2/JUDGE_OPERATIONS.md. The job probes the pin
# first and falls back to unpinned routing rather than failing the whole run.
#
# Submit (login node):  condor_submit_bid 100 cluster/run_askG_v17_judge.sub
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
then echo "pinning to GMICloud"; else echo "pin probe failed -> unpinned routing"; PIN_ARGS=(); fi

RUNS=(experiments/agent1/outputs/v17/inf_askG_deepseek_s301.json
      experiments/agent1/outputs/v17/inf_askG_kimi_s303.json
      experiments/agent1/outputs/v17/inf_askG_kimi_s304.json
      experiments/agent1/outputs/v17/inf_askG_glm_s305.json)
rc=0; i=0; pids=()
for f in "${RUNS[@]}"; do
  for r in 1 2 3; do
    tag="$(basename "$f" .json)_r${r}"
    "$PROJECT/.venv/bin/python" -m experiments.agent2.category2_over_agent1 "$f" \
      --provider openrouter --judge-model deepseek/deepseek-v4-flash-0731 \
      --workers 3 --baseline 0 --replicate "$r" \
      --roles "Data Scientist,Product Manager" "${PIN_ARGS[@]}" \
      > "$PROJECT/cluster/askG_v17_${tag}.log" 2>&1 &
    pids+=($!); i=$((i+1))
    if (( i % 8 == 0 )); then for p in "${pids[@]}"; do wait "$p" || rc=1; done; pids=(); fi
  done
done
for p in "${pids[@]}"; do wait "$p" || rc=1; done
echo "--- verdicts written (expect 12):"
for f in "${RUNS[@]}"; do ls "${f%.json}".category2_jv7* 2>/dev/null; done | wc -l
exit $rc
