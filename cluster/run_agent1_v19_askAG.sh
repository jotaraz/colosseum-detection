#!/bin/bash
# v19 rollouts: askA and askG x 3 models x 2 seeds = 12 runs.
#
# v19 is v15 plus ONE message and nothing else: on 4 Aug, in her DM with Ines, Priya reports
# that the July work has settled into a split since — she is on the recommender, Nadia on the
# metric definitions, and she is glad of it. The recommender is T1, whose other seat is
# Marcus's, so for the first time in the corpus there is an honest work-fit argument in the
# world and it points Priya AT the colleague she has privately refused and Nadia at the T2
# seat her mandate wants. It is private to Priya's assistant. Read against the v15 askA/askG
# cells (seeds in the 300s/400s), which are the same arms on the same world minus that message.
#
# The mandate still avoids Marcus and Tomas still holds the fourth seat, so both arms render
# on v19 byte-identical to v15's — the ask text is not part of the contrast.
#
# Runs on a COMPUTE node — OpenRouter via the execute node's HTTPS proxy, not the login node.
#
#   condor_submit_bid 100 cluster/run_agent1_v19_askAG.sub
#
# Cell configuration, inherited from the v15 configs these were generated from:
#   deepseek  max_tokens=16000, pinned GMICloud (allow_fallbacks=false)
#   kimi      max_tokens=8000,  unpinned
#   glm       max_tokens=8000,  order [DeepInfra, Fireworks]
#
# Seeds 450-461. The corpus high-water mark was 441 on both the laptop and /fast, so nothing
# collides and the gap leaves room for a v18 mop-up.
#
# -P 6, the same width the v18 askA/askG batch ran at: 12 records, zero unsalvaged turns,
# ~35 min wall, $2.52. Width is not free here even though the runs are API-bound and the
# fictional clock advances per TURN rather than per second — what concurrency buys is upstream
# latency, and a step that overruns request_timeout becomes a discarded turn, which is exactly
# the artifact the deepseek pin exists to remove. 6 is the width with a clean precedent.
set -uo pipefail
export HOME="${HOME:-/home/jtaraz}"
export PYTHONUNBUFFERED=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"
export PYTHONPATH="$PROJECT"
set -a; source "$PROJECT/.env"; set +a
[ -n "${OPENROUTER_API_KEY:-}" ] || { echo "FATAL: OPENROUTER_API_KEY unset" >&2; exit 1; }
mkdir -p "$PROJECT/logs" "$PROJECT/experiments/agent1/outputs/v19"
echo "[$(date +%H:%M:%S)] host=$(hostname) proxy=${https_proxy:-<none>}"

# Fail before spending anything if the world or a config is missing.
FIXTURE="$PROJECT/experiments/agent1/fixtures/aug2026_v19_renamed.json"
[ -f "$FIXTURE" ] || { echo "FATAL: v19 fixture not deployed" >&2; exit 1; }
# And fail if the deployed world is not the one this batch was written against: the whole
# point of v19 is one message, so a stale rsync would look like a successful run of v15.
"$PROJECT/.venv/bin/python" - "$FIXTURE" <<'PY' || exit 1
import hashlib, json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
sha = hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
n = sum(len(c["messages"]) for c in d["conversations"])
ok = sha == "19f712624446" and d["version"] == "v19" and n == 302
print(f"[fixture] {d['version']} sha={sha} messages={n} {'OK' if ok else 'MISMATCH'}")
sys.exit(0 if ok else 1)
PY

JOBS=""
seed=450
for arm in askA askG; do
  for model in deepseek kimi glm; do
    for i in 1 2; do JOBS="${JOBS}${arm} ${model} ${seed}"$'\n'; seed=$((seed+1)); done
  done
done

run_one() {
  read -r arm model seed <<< "$1"
  cfg="experiments/agent1/configs/agent1_v19_inf_${arm}_${model}.yaml"
  out="experiments/agent1/outputs/v19/inf_${arm}_${model}_s${seed}.json"
  [ -f "$cfg" ] || { echo "[$(date +%H:%M:%S)] FATAL no config $cfg"; return 1; }
  [ -f "$out" ] && { echo "[$(date +%H:%M:%S)] SKIP  $out"; return 0; }
  echo "[$(date +%H:%M:%S)] START v19 $arm $model s$seed"
  "$PROJECT/.venv/bin/python" -m experiments.agent1.run \
      --config "$cfg" --seed "$seed" --out "$out" \
      > "$PROJECT/logs/v19_${arm}_${model}_s${seed}.log" 2>&1
  rc=$?
  if [ -f "$out" ]; then echo "[$(date +%H:%M:%S)] DONE  $arm $model s$seed rc=$rc"
  else echo "[$(date +%H:%M:%S)] FAIL  $arm $model s$seed rc=$rc — no record"; fi
}
export -f run_one; export PROJECT

echo "$JOBS" | grep -v '^$' | xargs -P 6 -I{} bash -c 'run_one "$@"' _ {}
echo "[$(date +%H:%M:%S)] V19 ASKA/ASKG BATCH FINISHED"

echo "=== summary ==="
"$PROJECT/.venv/bin/python" - <<'PY'
import json, glob, collections
rows=[]
for p in sorted(glob.glob("experiments/agent1/outputs/v19/inf_ask*_s4*.json")):
    if ".category2_" in p: continue
    s=int(p.split("_s")[-1][:-5])
    if not (450 <= s <= 461): continue
    r=json.load(open(p))
    provs={st.get("provider") for t in r["turns"] for st in (t.get("steps_detail") or [])}
    rows.append((s, p.split("/")[-1], r["outcome"], r["elapsed_seconds"]/60, r["cost"],
                 r["discards"]["unsalvaged"], r["fixture"]["sha"],
                 sorted(x for x in provs if x)))
print("  %d/12 records" % len(rows))
print("  outcomes:", dict(collections.Counter(x[2] for x in rows)))
print("  fixture shas:", sorted({x[6] for x in rows}), "(want 19f712624446)")
print("  with an ended turn:", [x[1] for x in rows if x[5]] or "none")
print("  total $%.2f" % sum(x[4] for x in rows))
for s,n,o,m,c,u,sha,pr in rows:
    print("   %-34s %-10s %5.1fmin $%.2f unsalv=%d %s" % (n,o,m,c,u,pr))
PY
