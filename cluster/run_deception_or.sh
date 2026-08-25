#!/bin/bash
# jv10 (per step) and jv11 (per turn) — the deception judges — over the frozen 91-turn sample,
# on the two OpenRouter judge models. Azure's gpt-5.4 is a separate job (run_deception_azure).
#
#   condor_submit_bid 100 cluster/run_deception_or.sub
#
# THE SAMPLE is `experiments/agent2/deception_sample.json`, drawn once by
# build_deception_sample.py (seed 0) and committed: 9 agent1 hit turns, 20 agent1 turns jv9
# never judged (round-stratified), 52 turns = every Priya turn of 10 agent3 hit-rollouts, and
# 10 further agent3 hit turns. Every setting judges the SAME 91 turns, which is the whole point
# — jv10 vs jv11 differ only in the judged unit, so any disagreement is about unitisation.
#
# WHY SEQUENTIAL, 12 workers: 12 concurrent OpenRouter calls total was the requested budget.
# Eight parallel drivers at 12 workers each would be 96. Each of the 8 combinations
# (2 models x 2 versions x 2 replicates) writes a distinct file, so the ordering is free.
#
# REPLICATES are resamples, not seeds: make_judge_caller sends temperature 0, so replicate 2
# differs from replicate 1 only by provider nondeterminism. That is the same knob jv8/jv9's
# 3-replicate sweeps measured (and they did disagree), so it is comparable with them.
set -uo pipefail
export HOME="${HOME:-/home/jtaraz}"
export PYTHONUNBUFFERED=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"
export PYTHONPATH="$PROJECT"
set -a; source "$PROJECT/.env"; set +a
[ -n "${OPENROUTER_API_KEY:-}" ] || { echo "FATAL: OPENROUTER_API_KEY unset" >&2; exit 1; }
echo "[$(date +%H:%M:%S)] host=$(hostname) proxy=${https_proxy:-<none>}"

PY="$PROJECT/.venv/bin/python"
TARGETS="$PROJECT/experiments/agent2/deception_sample.json"
[ -f "$TARGETS" ] || { echo "FATAL: sample missing: $TARGETS" >&2; exit 1; }
echo "sample: $("$PY" -c "import json,sys;print(len(json.load(open(sys.argv[1]))))" "$TARGETS") turns"
LABEL="deception sample v1 (seed 0): 9 a1_hit + 20 a1_unjudged + 52 a3_full + 10 a3_extra"

# DeepSeek is pinned to GMICloud, retried three times before believing the backend is down:
# JUDGE_OPERATIONS.md — unpinned routing mixes upstream quantizations WITHIN a replicate set,
# adding exactly the variance the replicates exist to measure. A single failed probe is not
# evidence (2026-08-24: a one-shot probe returned finish_reason=stop with empty content).
PIN_ARGS=(--pin-provider GMICloud)
if "$PY" - <<'PY'
import json, os, sys, time, urllib.request
KEY = os.environ["OPENROUTER_API_KEY"]
def once():
    body = json.dumps({"model": "deepseek/deepseek-v4-flash-0731",
        "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
        "max_completion_tokens": 64,
        "provider": {"order": ["GMICloud"], "allow_fallbacks": False}}).encode()
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + KEY})
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.loads(r.read())
    ch = (d.get("choices") or [{}])[0]
    return bool(not d.get("error") and (ch.get("message") or {}).get("content")), d.get("provider"), ch.get("finish_reason")
for attempt in (1, 2, 3):
    try:
        ok, prov, fin = once()
        print(f"pin probe {attempt}/3: provider={prov} finish={fin} ok={ok}")
        if ok:
            sys.exit(0)
    except Exception as e:
        print(f"pin probe {attempt}/3 FAILED: {type(e).__name__}: {e}")
    if attempt < 3:
        time.sleep(10)
sys.exit(1)
PY
then echo "pinning deepseek to GMICloud"; else echo "pin probe failed 3x -> unpinned routing"; PIN_ARGS=(); fi

rc=0
for model in "deepseek/deepseek-v4-flash-0731" "z-ai/glm-5.2"; do
  # glm-5.2 has no pinned backend to defend, so routing stays free for it.
  extra=(); [ "$model" = "deepseek/deepseek-v4-flash-0731" ] \
    && extra=(${PIN_ARGS[@]+"${PIN_ARGS[@]}"})
  for version in jv10 jv11; do
    for r in 1 2; do
      slug="$(echo "$model" | tr '/.' '__')_${version}_r${r}"
      echo "[$(date +%H:%M:%S)] === $model $version replicate $r ==="
      "$PY" -m experiments.agent2.lie_over_agent1 \
          --targets "$TARGETS" --judge-version "$version" \
          --provider openrouter --judge-model "$model" \
          --workers 12 --replicate "$r" --selection-label "$LABEL" \
          ${extra[@]+"${extra[@]}"} \
          > "$PROJECT/cluster/deception_${slug}.log" 2>&1 || rc=1
      tail -2 "$PROJECT/cluster/deception_${slug}.log"
    done
  done
done
echo "[$(date +%H:%M:%S)] OPENROUTER DECEPTION SWEEP FINISHED rc=$rc"
"$PY" -m experiments.agent2.deception_summary 2>/dev/null || true
exit $rc
