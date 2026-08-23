#!/bin/bash
# Fix-up after the overnight v15 askG sweep:
#   1. quarantine the 9 stale verdicts on seeds 235/283/285 — their records were overwritten
#      at 02:32-02:42 but the verdicts date from 19-21 Aug, so they judge transcripts that no
#      longer exist. The judge is idempotent and skipped them, which is why they survived.
#   2. re-run deepseek s326, which died on a transient GMICloud HTTP 400 after 7 attempts.
#   3. judge all four (235, 283, 285, 326) x 3 replicates, pinned to GMICloud.
#
#   condor_submit_bid 100 cluster/run_agent1_v15_fixup.sub
set -uo pipefail
export HOME="${HOME:-/home/jtaraz}"; export PYTHONUNBUFFERED=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"; export PYTHONPATH="$PROJECT"
set -a; source "$PROJECT/.env"; set +a
[ -n "${OPENROUTER_API_KEY:-}" ] || { echo "FATAL: OPENROUTER_API_KEY unset" >&2; exit 1; }
mkdir -p "$PROJECT/logs"
OUT=$PROJECT/experiments/agent1/outputs/v15
echo "[$(date +%F\ %H:%M:%S)] fixup on $(hostname)"

# --- 1. quarantine stale verdicts (moved, not deleted: a re-judge may want to diff) --------
BK=$PROJECT/experiments/agent1/outputs/_stale_verdicts_20260822
mkdir -p "$BK"
n=0
for s in 235 283 285; do
  for v in $OUT/inf_askG_deepseek_s${s}.category2_*.json; do
    [ -e "$v" ] || continue
    mv "$v" "$BK/" && n=$((n+1))
  done
done
echo "[$(date +%H:%M:%S)] quarantined $n stale verdict(s) to ${BK#$PROJECT/}"

# --- 2. re-run the failed rollout ----------------------------------------------------------
out=$OUT/inf_askG_deepseek_s326.json
rm -f "$out" "${out%.json}.html"
echo "[$(date +%H:%M:%S)] re-running deepseek s326"
"$PROJECT/.venv/bin/python" -m experiments.agent1.run \
    --config experiments/agent1/configs/agent1_v15_inf_askG_deepseek.yaml \
    --seed 326 --out "$out" > "$PROJECT/logs/v15G_deepseek_s326_retry.log" 2>&1
rc=$?
if [ -f "$out" ]; then echo "[$(date +%H:%M:%S)] s326 DONE rc=$rc"
else echo "[$(date +%H:%M:%S)] s326 FAILED AGAIN rc=$rc — judging the other three only"; fi

# --- 3. judge the four --------------------------------------------------------------------
RUNS=()
for f in $OUT/inf_askG_deepseek_s235.json $OUT/inf_askG_deepseek_s283.json \
         $OUT/inf_askG_deepseek_s285.json $OUT/inf_askG_deepseek_s326.json; do
  [ -f "$f" ] && RUNS+=("$f")
done
echo "[$(date +%H:%M:%S)] judging ${#RUNS[@]} run(s) x 3 replicates"
for REP in 1 2 3; do
  echo "[$(date +%H:%M:%S)] === replicate $REP ==="
  "$PROJECT/.venv/bin/python" -m experiments.agent2.category2_over_agent1 \
      "${RUNS[@]}" --provider openrouter \
      --judge-model deepseek/deepseek-v4-flash-0731 \
      --pin-provider GMICloud --workers 3 --replicate "$REP" \
      > "$PROJECT/logs/jv7_fixup_r${REP}.log" 2>&1
  echo "[$(date +%H:%M:%S)] replicate $REP rc=$?"
done

echo "[$(date +%F\ %H:%M:%S)] FIXUP FINISHED"
"$PROJECT/.venv/bin/python" - <<'PY'
import json, glob, os, collections, datetime
seeds = set(str(s) for s in range(319, 349)) | {"235", "283", "285"}
recs = 0; short = []; stale = []; provs = collections.Counter(); failed = 0
for p in sorted(glob.glob("experiments/agent1/outputs/v15/inf_askG_*.json")):
    if ".category2_" in p: continue
    s = p.split("_s")[-1][:-5]
    if s not in seeds: continue
    recs += 1
    stem = p[:-5]
    vs = sorted(glob.glob(stem + ".category2_jv7_*.json"))
    if len(vs) != 3: short.append((os.path.basename(p), len(vs)))
    for v in vs:
        if os.path.getmtime(v) < os.path.getmtime(p): stale.append(os.path.basename(v))
        j = json.load(open(v)); failed += j.get("n_failed_turns") or 0
        for t in j.get("turns", []):
            for k, val in t.items():
                if k.startswith("judge_") and isinstance(val, dict):
                    pn = ((val.get("_meta") or {}).get("usage") or {}).get("provider_name")
                    if pn: provs[pn] += 1
print("  records present: %d / 33" % recs)
print("  runs without exactly 3 verdicts:", short or "none")
print("  verdicts older than their record:", stale or "none")
print("  failed turns:", failed)
print("  judge providers:", dict(provs))
PY
