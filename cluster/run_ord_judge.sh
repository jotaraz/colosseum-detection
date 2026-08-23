#!/bin/bash
# jv7 judging (CRITIC_CATEGORY6 + considered + absence verifier) of the two v17 ord* rollouts,
# 3 replicates each, executed ON A COMPUTE NODE so all OpenRouter traffic leaves via the node's
# HTTP(S) proxy — none from the login node or a workstation.
#
# Provider pinning: GMICloud is the evidence-backed pin (see experiments/agent2/
# JUDGE_OPERATIONS.md — unpinned evening routing scattered across ~30 upstreams at ~500s/turn;
# pinned ran at 13-18s/turn). The proxy path here is different from the workstation's, so the
# job PROBES the pin first and falls back to unpinned routing rather than failing the whole run.
#
# Submit (login node):  condor_submit_bid 100 cluster/run_ord_judge.sub
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
    "max_completion_tokens":16,
    "provider":{"order":["GMICloud"],"allow_fallbacks":False}}).encode()
req=urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body,
    headers={"Content-Type":"application/json",
             "Authorization":f"Bearer {os.environ['OPENROUTER_API_KEY']}"})
try:
    with urllib.request.urlopen(req, timeout=90) as r:
        d=json.loads(r.read())
    ok = not d.get("error") and (d.get("choices") or [{}])[0].get("message",{}).get("content")
    print(f"pin probe: provider={d.get('provider')} ok={bool(ok)}")
    sys.exit(0 if ok else 1)
except Exception as e:
    print(f"pin probe FAILED: {type(e).__name__}: {e}"); sys.exit(1)
PY
then echo "pinning to GMICloud"; else echo "pin probe failed -> unpinned routing"; PIN_ARGS=(); fi

RUNS=(experiments/agent1/outputs/v17/inf_askI_ordNMPR_deepseek_s319.json
      experiments/agent1/outputs/v17/inf_askI_ordNMPR_kimi_s322.json)
pids=()
for f in "${RUNS[@]}"; do
  for r in 1 2 3; do
    tag="$(basename "$f" .json)_r${r}"
    "$PROJECT/.venv/bin/python" -m experiments.agent2.category2_over_agent1 "$f" \
      --provider openrouter --judge-model deepseek/deepseek-v4-flash-0731 \
      --workers 3 --baseline 0 --replicate "$r" \
      --roles "Data Scientist,Product Manager" "${PIN_ARGS[@]}" \
      > "$PROJECT/cluster/ord_judge_${tag}.log" 2>&1 &
    pids+=($!)
  done
done
rc=0; for p in "${pids[@]}"; do wait "$p" || rc=1; done
echo "--- verdict files written:"
ls -l "$PROJECT"/experiments/agent1/outputs/v17/inf_askI_ordNMPR_*category2_jv7* 2>/dev/null || true
exit $rc
