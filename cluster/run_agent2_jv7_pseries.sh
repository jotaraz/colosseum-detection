#!/bin/bash
# Wait for the v15 p-series rollouts (cluster 17474040), then 3 jv7 judge replicates over them.
#
# Roles: the default, "Data Scientist" — in v15 that is exactly Priya and Nadia, the two turn
# owners we want judged. Passed explicitly so the intent is legible in the command, not only
# in the default.
#
#   condor_submit_bid 100 cluster/run_agent2_jv7_pseries.sub
set -uo pipefail
export HOME="${HOME:-/home/jtaraz}"; export PYTHONUNBUFFERED=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"; export PYTHONPATH="$PROJECT"
set -a; source "$PROJECT/.env"; set +a
[ -n "${OPENROUTER_API_KEY:-}" ] || { echo "FATAL: OPENROUTER_API_KEY unset" >&2; exit 1; }
mkdir -p "$PROJECT/logs"
OUT=$PROJECT/experiments/agent1/outputs/v15
ROLLOUT_LOG=$PROJECT/cluster/agent1_pser_17474045.log
echo "[$(date +%F\ %H:%M:%S)] judge job on $(hostname), proxy=${https_proxy:-<none>}"

targets() { for s in $(seq 349 396); do ls $OUT/inf_ask*_s${s}.json 2>/dev/null | grep -v category2; done; }

# Gate: all 48 present, or the rollout job has left the queue (judge what exists), or 8h cap.
deadline=$(( $(date +%s) + 8*3600 ))
while :; do
  n=$(targets | wc -l | tr -d ' ')
  fin=0; grep -q "Job terminated" "$ROLLOUT_LOG" 2>/dev/null && fin=1
  echo "[$(date +%H:%M:%S)] gate: $n/48 records, rollout finished=$fin"
  [ "$n" -ge 48 ] && { echo "[$(date +%H:%M:%S)] gate open: all present"; break; }
  [ "$fin" -eq 1 ] && { echo "[$(date +%H:%M:%S)] gate open: rollouts done with $n/48"; break; }
  [ "$(date +%s)" -ge "$deadline" ] && { echo "[$(date +%H:%M:%S)] gate open: 8h cap, $n/48"; break; }
  sleep 60
done

mapfile -t RUNS < <(targets)
[ "${#RUNS[@]}" -eq 0 ] && { echo "FATAL: nothing to judge"; exit 1; }
echo "[$(date +%H:%M:%S)] judging ${#RUNS[@]} runs x 3 replicates, roles='Data Scientist' (Priya+Nadia)"

# Probe GMICloud before pinning (JUDGE_OPERATIONS.md: check, don't assume). We pin either way:
# with fallbacks off a degraded pin fails fast, failures are per-turn, re-judging is idempotent.
"$PROJECT/.venv/bin/python" - <<'PY' 2>&1 | sed 's/^/  probe: /'
import os, time, json, urllib.request
body=json.dumps({"model":"deepseek/deepseek-v4-flash-0731",
 "messages":[{"role":"user","content":"Reply with the single word OK."}],"max_tokens":16,
 "provider":{"order":["GMICloud"],"allow_fallbacks":False}}).encode()
req=urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",data=body,
 headers={"Authorization":"Bearer "+os.environ["OPENROUTER_API_KEY"],"Content-Type":"application/json"})
t0=time.time()
try:
    d=json.load(urllib.request.urlopen(req,timeout=120))
    print(f"{time.time()-t0:.1f}s provider={d.get('provider')}")
except Exception as e:
    print(f"FAILED {type(e).__name__}: {e} — proceeding pinned anyway")
PY

for REP in 1 2 3; do
  echo "[$(date +%F\ %H:%M:%S)] === replicate $REP ==="
  "$PROJECT/.venv/bin/python" -m experiments.agent2.category2_over_agent1 \
      "${RUNS[@]}" --provider openrouter \
      --judge-model deepseek/deepseek-v4-flash-0731 \
      --pin-provider GMICloud --workers 3 \
      --roles "Data Scientist" --replicate "$REP" \
      > "$PROJECT/logs/jv7_pseries_r${REP}.log" 2>&1
  echo "[$(date +%H:%M:%S)] replicate $REP rc=$?"
done

echo "[$(date +%F\ %H:%M:%S)] P-SERIES JUDGING FINISHED"
"$PROJECT/.venv/bin/python" - <<'PY'
import json, glob, os, collections
recs=0; short=[]; stale=[]; provs=collections.Counter(); failed=0; judged=collections.Counter()
for p in sorted(glob.glob("experiments/agent1/outputs/v15/inf_ask*.json")):
    if ".category2_" in p: continue
    s=int(p.split("_s")[-1][:-5])
    if not (349 <= s <= 396): continue
    recs+=1
    vs=sorted(glob.glob(p[:-5]+".category2_jv7_*.json"))
    if len(vs)!=3: short.append((os.path.basename(p), len(vs)))
    for v in vs:
        if os.path.getmtime(v) < os.path.getmtime(p): stale.append(os.path.basename(v))
        j=json.load(open(v)); failed += j.get("n_failed_turns") or 0
        for t in j.get("turns",[]):
            who=t.get("agent") or t.get("employee")
            if who: judged[who]+=1
            for k,val in t.items():
                if k.startswith("judge_") and isinstance(val,dict):
                    pn=((val.get("_meta") or {}).get("usage") or {}).get("provider_name")
                    if pn: provs[pn]+=1
print("  records: %d / 48" % recs)
print("  runs without exactly 3 verdicts:", short or "none")
print("  verdicts older than their record:", stale or "none")
print("  failed turns:", failed)
print("  turns judged, by principal:", dict(judged))
print("  judge providers:", dict(provs))
PY
