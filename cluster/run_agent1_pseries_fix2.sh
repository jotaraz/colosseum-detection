#!/bin/bash
# Two residual fixes on the v15 p-series:
#   1. re-run askL kimi s358 — one turn was ended by the 8000-token cap, so that turn is not
#      evidence about the model. Overwritten in place; its 3 verdicts are removed first, or
#      they would judge a transcript that no longer exists (the trap that bit twice already).
#   2. re-judge askP5 kimi s394 replicate 3 with --force — 6 turn-judgements were lost to
#      GMICloud returning HTTP 402 "Insufficient balance" (its balance, not ours), and with
#      allow_fallbacks:false there was nowhere to reroute.
#
#   condor_submit_bid 100 cluster/run_agent1_pseries_fix2.sub
set -uo pipefail
export HOME="${HOME:-/home/jtaraz}"; export PYTHONUNBUFFERED=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"; export PYTHONPATH="$PROJECT"
set -a; source "$PROJECT/.env"; set +a
[ -n "${OPENROUTER_API_KEY:-}" ] || { echo "FATAL: OPENROUTER_API_KEY unset" >&2; exit 1; }
mkdir -p "$PROJECT/logs"
OUT=$PROJECT/experiments/agent1/outputs/v15
echo "[$(date +%F\ %H:%M:%S)] fix2 on $(hostname)"

# --- probe GMICloud: it ran out of balance at 02:02, so this one is not a formality --------
echo "[$(date +%H:%M:%S)] probing GMICloud"
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
    print(f"OK {time.time()-t0:.1f}s provider={d.get('provider')}")
except Exception as e:
    print(f"FAILED {type(e).__name__}: {e}")
    print("NOTE if this is another 402, the re-judge below will fail the same way.")
PY

# --- 1. re-run the capped rollout -----------------------------------------------------------
STEM=$OUT/inf_askL_kimi_s358
echo "[$(date +%H:%M:%S)] removing $(ls $STEM.category2_*.json 2>/dev/null | wc -l) verdict(s) for s358, then re-running"
BK=$PROJECT/experiments/agent1/outputs/_stale_verdicts_20260823
mkdir -p "$BK"; for v in $STEM.category2_*.json; do [ -e "$v" ] && mv "$v" "$BK/"; done
rm -f "$STEM.json" "$STEM.html"
"$PROJECT/.venv/bin/python" -m experiments.agent1.run \
    --config experiments/agent1/configs/agent1_v15_inf_askL_kimi.yaml \
    --seed 358 --out "$STEM.json" > "$PROJECT/logs/fix2_askL_kimi_s358.log" 2>&1
rc=$?
[ -f "$STEM.json" ] && echo "[$(date +%H:%M:%S)] s358 rerun DONE rc=$rc" || echo "[$(date +%H:%M:%S)] s358 rerun FAILED rc=$rc"

# --- 2. judge s358 fresh (3 reps) and re-judge s394 r3 with --force -------------------------
for REP in 1 2 3; do
  [ -f "$STEM.json" ] || break
  "$PROJECT/.venv/bin/python" -m experiments.agent2.category2_over_agent1 "$STEM.json" \
      --provider openrouter --judge-model deepseek/deepseek-v4-flash-0731 \
      --pin-provider GMICloud --workers 3 --roles "Data Scientist" --replicate "$REP" \
      > "$PROJECT/logs/fix2_jv7_s358_r${REP}.log" 2>&1
  echo "[$(date +%H:%M:%S)] s358 replicate $REP rc=$?"
done

echo "[$(date +%H:%M:%S)] re-judging s394 replicate 3 (--force)"
"$PROJECT/.venv/bin/python" -m experiments.agent2.category2_over_agent1 \
    "$OUT/inf_askP5_kimi_s394.json" \
    --provider openrouter --judge-model deepseek/deepseek-v4-flash-0731 \
    --pin-provider GMICloud --workers 3 --roles "Data Scientist" --replicate 3 --force \
    > "$PROJECT/logs/fix2_jv7_s394_r3.log" 2>&1
echo "[$(date +%H:%M:%S)] s394 r3 rc=$?"

echo "[$(date +%F\ %H:%M:%S)] FIX2 FINISHED"
"$PROJECT/.venv/bin/python" - <<'PY'
import json, glob, os, collections
recs=0; short=[]; stale=[]; unsalv=[]; failed={}; provs=collections.Counter(); judged=collections.Counter()
for p in sorted(glob.glob("experiments/agent1/outputs/v15/inf_ask*.json")):
    if ".category2_" in p: continue
    s=int(p.split("_s")[-1][:-5])
    if not (349 <= s <= 396): continue
    recs+=1
    r=json.load(open(p))
    if r["discards"]["unsalvaged"]: unsalv.append(os.path.basename(p))
    vs=sorted(glob.glob(p[:-5]+".category2_jv7_*.json"))
    if len(vs)!=3: short.append((os.path.basename(p),len(vs)))
    for v in vs:
        if os.path.getmtime(v)<os.path.getmtime(p): stale.append(os.path.basename(v))
        j=json.load(open(v))
        nf=(j.get("diagnostics") or {}).get("n_failed_turns") or 0
        if nf: failed[os.path.basename(v)]=nf
        for t in j.get("turns",[]):
            w=t.get("agent") or t.get("employee")
            if w: judged[w]+=1
            for k,val in t.items():
                if k.startswith("judge_") and isinstance(val,dict):
                    pn=((val.get("_meta") or {}).get("usage") or {}).get("provider_name")
                    if pn: provs[pn]+=1
print("  records: %d/48 | 3 verdicts each: %s" % (recs, "yes" if not short else short))
print("  stale: %s" % (stale or "none"))
print("  rollouts with an ended turn: %s" % (unsalv or "none"))
print("  verdicts with FAILED judge turns: %s" % (failed or "none"))
print("  turns judged: %s" % dict(judged))
print("  providers: %s" % dict(provs))
PY
