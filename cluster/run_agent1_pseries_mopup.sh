#!/bin/bash
# Mop-up for the v15 p-series: re-run whichever rollouts died, then judge them.
#
# Gated on BOTH the rollout job (17474045) and its chained judge (17474046) having left the
# queue. Waiting for the judge matters: it judges whatever exists when it opens its gate, so a
# re-run that lands mid-sweep would be missed, and re-judging it here would race.
#
# Discovers the failures from the rollout log rather than hard-coding a seed, so it stays
# correct if more than askP1/s367 dies before the batch ends.
#
#   condor_submit_bid 100 cluster/run_agent1_pseries_mopup.sub
set -uo pipefail
export HOME="${HOME:-/home/jtaraz}"; export PYTHONUNBUFFERED=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"; export PYTHONPATH="$PROJECT"
set -a; source "$PROJECT/.env"; set +a
[ -n "${OPENROUTER_API_KEY:-}" ] || { echo "FATAL: OPENROUTER_API_KEY unset" >&2; exit 1; }
mkdir -p "$PROJECT/logs"
OUT=$PROJECT/experiments/agent1/outputs/v15
ROLL_LOG=$PROJECT/cluster/agent1_pser_17474045.log
JUDGE_LOG=$PROJECT/cluster/agent2_jv7pser_17474046.log
ROLL_OUT=$PROJECT/cluster/agent1_pser_17474045.out
echo "[$(date +%F\ %H:%M:%S)] mopup on $(hostname)"

deadline=$(( $(date +%s) + 10*3600 ))
while :; do
  r=0; j=0
  grep -q "Job terminated" "$ROLL_LOG"  2>/dev/null && r=1
  grep -q "Job terminated" "$JUDGE_LOG" 2>/dev/null && j=1
  echo "[$(date +%H:%M:%S)] gate: rollouts_done=$r judge_done=$j"
  [ "$r" -eq 1 ] && [ "$j" -eq 1 ] && break
  [ "$(date +%s)" -ge "$deadline" ] && { echo "[$(date +%H:%M:%S)] 10h cap"; break; }
  sleep 120
done

# Missing records among seeds 349-396 — the authoritative list, whatever the log says.
MISSING=()
for s in $(seq 349 396); do
  ls $OUT/inf_ask*_s${s}.json >/dev/null 2>&1 || MISSING+=("$s")
done
echo "[$(date +%H:%M:%S)] missing records: ${MISSING[*]:-none}"
[ "${#MISSING[@]}" -eq 0 ] && { echo "[$(date +%H:%M:%S)] nothing to mop up"; exit 0; }

# Recover each missing seed's arm+model from the rollout log's START lines.
RUNS=()
for s in "${MISSING[@]}"; do
  spec=$(grep -E "START ask.* s${s}$" "$ROLL_OUT" | tail -1 | sed 's/.*START //')
  arm=$(echo "$spec" | awk '{print $1}'); model=$(echo "$spec" | awk '{print $2}')
  [ -z "$arm" ] && { echo "  cannot resolve seed $s from the log — skipping"; continue; }
  out="$OUT/inf_${arm}_${model}_s${s}.json"
  echo "[$(date +%H:%M:%S)] re-running $arm $model s$s"
  rm -f "$out" "${out%.json}.html"
  "$PROJECT/.venv/bin/python" -m experiments.agent1.run \
      --config "experiments/agent1/configs/agent1_v15_inf_${arm}_${model}.yaml" \
      --seed "$s" --out "$out" > "$PROJECT/logs/mopup_${arm}_${model}_s${s}.log" 2>&1
  rc=$?
  if [ -f "$out" ]; then echo "[$(date +%H:%M:%S)] DONE $arm $model s$s rc=$rc"; RUNS+=("$out")
  else echo "[$(date +%H:%M:%S)] STILL FAILING $arm $model s$s rc=$rc"; fi
done

[ "${#RUNS[@]}" -eq 0 ] && { echo "[$(date +%H:%M:%S)] no re-runs succeeded — nothing to judge"; exit 1; }
echo "[$(date +%H:%M:%S)] judging ${#RUNS[@]} recovered run(s) x 3 replicates"
for REP in 1 2 3; do
  "$PROJECT/.venv/bin/python" -m experiments.agent2.category2_over_agent1 \
      "${RUNS[@]}" --provider openrouter \
      --judge-model deepseek/deepseek-v4-flash-0731 \
      --pin-provider GMICloud --workers 3 \
      --roles "Data Scientist" --replicate "$REP" \
      > "$PROJECT/logs/jv7_mopup_r${REP}.log" 2>&1
  echo "[$(date +%H:%M:%S)] replicate $REP rc=$?"
done

echo "[$(date +%F\ %H:%M:%S)] MOPUP FINISHED"
"$PROJECT/.venv/bin/python" - <<'PY'
import json, glob, os, collections
recs=0; short=[]; stale=[]; provs=collections.Counter(); failed=0; unsalv=[]; judged=collections.Counter()
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
        j=json.load(open(v)); failed+=j.get("n_failed_turns") or 0
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
print("  rollouts with a turn ended by the cap:", unsalv or "none")
print("  failed judge turns:", failed)
print("  turns judged, by principal:", dict(judged))
print("  judge providers:", dict(provs))
PY
