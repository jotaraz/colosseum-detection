#!/bin/bash
# jv8 (CRITIC_LIE1.md, the lie judge) over EVERY Priya turn in v15's askA and askG arms.
# 3 replicates of deepseek-v4-flash-0731, --workers 4, on a compute node so all OpenRouter
# traffic leaves via the node's HTTPS proxy.
#
#   condor_submit_bid 100 cluster/run_jv8_priya_v15.sub
#
# WHY UNGATED: jv8 is target-driven (unlike jv7, which sweeps a run's stake turns), so the
# target list IS the selection. Judging every Priya turn — not only the ones jv7 flagged —
# gives a denominator, which is what lets a lie *rate* per arm/model be computed at all.
# Targets are built by experiments/agent2/build_priya_targets.py, whose indices come from
# assemble_turns(), the same resolver lie_over_agent1 looks targets up with.
#
# NOT --force, by choice (2026-08-24): nine v15 runs already carry lie_jv8 files from the
# earlier hard-fabrication campaign, covering 36 Priya turns. The driver keeps already-judged
# turns and judges only what is missing, so those 36 are not re-spent — at the cost that those
# nine files end up a union of two selections rather than one. Their `selection` string will
# still read as the FIRST campaign's, because the file is merged into, not rewritten.
#
# The 51 files written fresh get an accurate `selection` via --selection-label (added with
# that flag on 2026-08-24; previously the string was hardcoded to the first campaign's).
set -uo pipefail
export HOME="${HOME:-/home/jtaraz}"
export PYTHONUNBUFFERED=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"
export PYTHONPATH="$PROJECT"
set -a; source "$PROJECT/.env"; set +a
[ -n "${OPENROUTER_API_KEY:-}" ] || { echo "FATAL: OPENROUTER_API_KEY unset" >&2; exit 1; }
echo "[$(date +%H:%M:%S)] host=$(hostname) proxy=${https_proxy:-<none>}"

TARGETS="$PROJECT/experiments/agent2/jv8_priya_v15_targets.json"
[ -f "$TARGETS" ] || { echo "FATAL: targets file missing: $TARGETS" >&2; exit 1; }
echo "targets: $("$PROJECT/.venv/bin/python" -c "import json,sys;print(len(json.load(open(sys.argv[1]))))" "$TARGETS")"

PIN_ARGS=(--pin-provider GMICloud)
# The probe RETRIES. A single call is not evidence about a backend: on 2026-08-24 a one-shot
# probe came back finish_reason=stop with empty content — the same transient empty-reply mode
# the rollouts hit — and the job silently dropped to unpinned routing. For a 3-replicate
# test-retest campaign that is the wrong default: JUDGE_OPERATIONS.md notes unpinned judging
# mixes upstream quantizations WITHIN a replicate set, i.e. it adds exactly the variance the
# replicates exist to measure. Three strikes before believing GMICloud is really unavailable.
if "$PROJECT/.venv/bin/python" - <<'PY'
import json, os, sys, time, urllib.request
KEY = os.environ["OPENROUTER_API_KEY"]
def once():
    body=json.dumps({"model":"deepseek/deepseek-v4-flash-0731",
        "messages":[{"role":"user","content":"Reply with the single word: ok"}],
        "max_completion_tokens":64,
        "provider":{"order":["GMICloud"],"allow_fallbacks":False}}).encode()
    req=urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Content-Type":"application/json","Authorization":"Bearer "+KEY})
    with urllib.request.urlopen(req, timeout=90) as r: d=json.loads(r.read())
    ch=(d.get("choices") or [{}])[0]
    ok = not d.get("error") and (ch.get("message") or {}).get("content")
    return bool(ok), d.get("provider"), ch.get("finish_reason")
for attempt in (1, 2, 3):
    try:
        ok, prov, fin = once()
        print(f"pin probe {attempt}/3: provider={prov} finish={fin} ok={ok}")
        if ok: sys.exit(0)
    except Exception as e:
        print(f"pin probe {attempt}/3 FAILED: {type(e).__name__}: {e}")
    if attempt < 3: time.sleep(10)
sys.exit(1)
PY
then echo "pinning to GMICloud"; else echo "pin probe failed 3x -> unpinned routing"; PIN_ARGS=(); fi

LABEL="every Priya turn — v15 askA+askG, ungated"
rc=0; pids=()
for r in 1 2 3; do
  "$PROJECT/.venv/bin/python" -m experiments.agent2.lie_over_agent1 \
      --targets "$TARGETS" \
      --provider openrouter --judge-model deepseek/deepseek-v4-flash-0731 \
      --workers 4 --replicate "$r" --selection-label "$LABEL" "${PIN_ARGS[@]}" \
      > "$PROJECT/cluster/jv8_priya_v15_r${r}.log" 2>&1 &
  pids+=($!)
done
for p in "${pids[@]}"; do wait "$p" || rc=1; done
echo "[$(date +%H:%M:%S)] JV8 PRIYA V15 FINISHED rc=$rc"

echo "=== summary ==="
"$PROJECT/.venv/bin/python" - <<'PY'
import json, glob, collections
files=[f for f in glob.glob("experiments/agent1/outputs/v15/inf_ask[AG]_*.lie_jv8_*.json")]
print("  verdict files:", len(files))
turns=collections.Counter(); lies=collections.Counter()
for f in files:
    d=json.load(open(f)); rep=d.get("replicate")
    for t in d.get("turns") or []:
        if t.get("agent")!="Priya": continue
        turns[rep]+=1; lies[rep]+=1 if t.get("n_lies") else 0
for rep in sorted(turns):
    print(f"   replicate {rep}: {turns[rep]:>4} Priya turns, {lies[rep]:>3} with >=1 lie")
PY
exit $rc
