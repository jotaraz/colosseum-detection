#!/bin/bash
# Wait for the v18 askA/askG rollouts, then run 3 jv7 judge replicates over their 12 runs.
#
# Self-gating on purpose: it polls for the rollout records itself rather than being launched by
# hand afterwards, so the whole chain completes unattended.
#
# Gate: proceed when all 12 records exist, OR when the rollout job has left the queue (its
# condor .log shows "Job terminated") — the second arm means a partial rollout still gets its
# finished runs judged instead of blocking forever. Hard cap 6h.
#
# The judge is deepseek-v4-flash-0731 pinned to GMICloud, matching the deepseek rollout cells.
#
#   condor_submit_bid 100 cluster/run_agent2_jv7_v18.sub
set -uo pipefail
export HOME="${HOME:-/home/jtaraz}"; export PYTHONUNBUFFERED=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"; export PYTHONPATH="$PROJECT"
set -a; source "$PROJECT/.env"; set +a
[ -n "${OPENROUTER_API_KEY:-}" ] || { echo "FATAL: OPENROUTER_API_KEY unset" >&2; exit 1; }
mkdir -p "$PROJECT/logs"
echo "[$(date +%F\ %H:%M:%S)] judge job on $(hostname), proxy=${https_proxy:-<none>}"

OUT=$PROJECT/experiments/agent1/outputs/v18
ROLLOUT_LOGS="/fast/jtaraz/LIARS/colosseum-detection/cluster/agent1_v18AG_17475477.log"

targets() {   # the 12: askA/askG x deepseek/kimi/glm x 2 seeds, 400-411
  for s in $(seq 400 411); do
    for a in askA askG; do
      for m in deepseek kimi glm; do
        f="$OUT/inf_${a}_${m}_s${s}.json"; [ -f "$f" ] && echo "$f"
      done
    done
  done
}

# ---------------------------------------------------------------- gate
deadline=$(( $(date +%s) + 6*3600 ))
while :; do
  n=$(targets | wc -l | tr -d ' ')
  done_jobs=0
  for L in $ROLLOUT_LOGS; do grep -q "Job terminated" "$L" 2>/dev/null && done_jobs=$((done_jobs+1)); done
  echo "[$(date +%H:%M:%S)] gate: $n/12 records, $done_jobs/1 rollout jobs finished"
  [ "$n" -ge 12 ] && { echo "[$(date +%H:%M:%S)] gate open: all records present"; break; }
  [ "$done_jobs" -ge 1 ] && { echo "[$(date +%H:%M:%S)] gate open: rollouts finished with $n/12 — judging what exists"; break; }
  [ "$(date +%s)" -ge "$deadline" ] && { echo "[$(date +%H:%M:%S)] gate open: 6h cap hit with $n/12"; break; }
  sleep 60
done

mapfile -t RUNS < <(targets)
echo "[$(date +%H:%M:%S)] judging ${#RUNS[@]} runs x 3 replicates"
[ "${#RUNS[@]}" -eq 0 ] && { echo "FATAL: no records to judge"; exit 1; }

# ------------------------------------------------- probe GMICloud before pinning
# JUDGE_OPERATIONS.md: a router avoiding a backend *can* mean it is saturated — check.
# We proceed pinned either way: with allow_fallbacks off a degraded pin fails fast, failures
# are recorded per turn, and re-judging is idempotent, so fail-fast beats silent rerouting.
echo "[$(date +%H:%M:%S)] probing GMICloud..."
"$PROJECT/.venv/bin/python" - <<'PY' 2>&1 | sed 's/^/  probe: /'
import os, time, json, urllib.request
body = json.dumps({"model": "deepseek/deepseek-v4-flash-0731",
                   "messages": [{"role": "user", "content": "Reply with the single word OK."}],
                   "max_tokens": 16,
                   "provider": {"order": ["GMICloud"], "allow_fallbacks": False}}).encode()
req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"],
                 "Content-Type": "application/json"})
t0 = time.time()
try:
    d = json.load(urllib.request.urlopen(req, timeout=120))
    print(f"{time.time()-t0:.1f}s provider={d.get('provider')} text={d['choices'][0]['message']['content'][:20]!r}")
except Exception as e:
    print(f"FAILED after {time.time()-t0:.1f}s: {type(e).__name__}: {e} — proceeding pinned anyway (fail-fast)")
PY

# ---------------------------------------------------------------- judge
for REP in 1 2 3; do
  echo "[$(date +%F\ %H:%M:%S)] === replicate $REP ==="
  "$PROJECT/.venv/bin/python" -m experiments.agent2.category2_over_agent1 \
      "${RUNS[@]}" \
      --provider openrouter \
      --judge-model deepseek/deepseek-v4-flash-0731 \
      --pin-provider GMICloud \
      --workers 3 \
      --replicate "$REP" \
      > "$PROJECT/logs/jv7_v18_r${REP}.log" 2>&1
  echo "[$(date +%H:%M:%S)] replicate $REP rc=$? — $(ls $OUT/*.category2_jv7_*.json 2>/dev/null | wc -l) verdict files in v18 now"
done

echo "[$(date +%F\ %H:%M:%S)] JV7 JUDGE SWEEP FINISHED"
"$PROJECT/.venv/bin/python" - <<'PY'
import json, glob, collections
seeds = set(str(s) for s in range(400, 412))
have = collections.Counter(); provs = collections.Counter(); failed = 0
for p in glob.glob("experiments/agent1/outputs/v18/inf_ask*.category2_jv7_*.json"):
    stem = p.split("/")[-1].split(".category2_")[0]
    s = stem.split("_s")[-1]
    if s not in seeds: continue
    have[stem] += 1
    j = json.load(open(p))
    failed += j.get("n_failed_turns") or 0
    for t in j.get("turns", []):
        for k, v in t.items():
            if k.startswith("judge_") and isinstance(v, dict):
                pn = ((v.get("_meta") or {}).get("usage") or {}).get("provider_name")
                if pn: provs[pn] += 1
print("  runs with verdicts: %d (expected 12)" % len(have))
short = {k: v for k, v in have.items() if v != 3}
print("  runs without exactly 3 replicates:", short or "none")
print("  failed turns across all verdicts:", failed)
print("  judge providers seen:", dict(provs))
PY
