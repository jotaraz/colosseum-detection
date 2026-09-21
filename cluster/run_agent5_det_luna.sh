#!/bin/bash
# agent5 DETECTION judge (detection_judge5) with gpt-5.6-luna on the OpenRouter .env3 account and
# critic v2, both variants in a chain (reasoning, no_reasoning), on a CPU compute node.
# Written 2026-09-21. Sibling of run_agent5_dd_judge.sh, which stays on bifrost gpt-5.5 + critic
# v1 and writes detection_5e8_v1; this one writes detection_5e8_v2 and the two are never pooled.
#
#   condor_submit_bid 15 cluster/run_agent5_det_luna.sub
#   condor_submit_bid 15 cluster/run_agent5_det_luna.sub variants=reasoning workers=2
#   condor_submit_bid 15 cluster/run_agent5_det_luna.sub models="deepseek glm53" seeds="0 1 2"
#
# Default set: s0 and s1 of each of the 8 OPEN-WEIGHT models of 5.e.viii = 16 runs, 5 seats, both
# variants = 160 calls (~61k tokens each by the v1 run, so ~10M tokens). gpt55gw and opus5cli are
# hosted models and ablarge2 is the abliterated probe, so none of the three is in the default set.
# deepseek s0/s1 and glm53flash s0/s1 are also in detection_5e8_v1, which is the v1-vs-v2 read.
# Note kimi is served without chain-of-thought, so its two variants see the same record.
#
# The .env3 account is capped at 20 requests/minute, so run-workers=1 x workers=2 is the steady
# state. A call that 429s after the client's retries leaves `judge_error` in that seat's file and
# detection_judge5 re-judges exactly those seats on the next pass — hence two passes per variant,
# no rows.partial surgery (unlike run_agent5_luna_judge.sh, whose judge is row-based).
export HOME="${HOME:-/home/jtaraz}"
set -uo pipefail
export PYTHONUNBUFFERED=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"
CRITIC="${1:-v2}"
OUT="${2:-experiments/agent5/outputs/detection_5e8_v2}"
VARIANTS="${3:-reasoning no_reasoning}"
WORKERS="${4:-2}"
MODELS="${5:-deepseek dspro glm glm53 glm53flash kimi kimik3 qwen38flash}"
SEEDS="${6:-0 1}"

JUDGE="openrouter:openai/gpt-5.6-luna"
export OPENROUTER_API_KEY_FILE="$PROJECT/experiments/agent5/.env3"
PROMPT="$PROJECT/experiments/agent5/CRITIC_DETECTION_V2.md"

# NFS reads of the key file have failed transiently on some nodes (see run_agent5_disc_judge.sh).
for attempt in 1 2 3 4 5 6; do
  KEY3="$(tr -d ' \r\n' < "$OPENROUTER_API_KEY_FILE" 2>/dev/null)"
  [ -n "${KEY3:-}" ] && [ -f "$PROMPT" ] && break
  echo "key/prompt load attempt $attempt failed, retrying in 20s" >&2; sleep 20
done
[ -n "${KEY3:-}" ] || { echo "FATAL: $OPENROUTER_API_KEY_FILE unreadable or empty" >&2; exit 1; }
[ -f "$PROMPT" ]   || { echo "FATAL: $PROMPT missing" >&2; exit 1; }
unset KEY3  # the judge reads the file itself; do not leave the key in the environment

"$PROJECT/.venv/bin/python" - "$JUDGE" <<'PY'
import sys
sys.path.insert(0, "/fast/jtaraz/LIARS/colosseum-detection")
from experiments.agent5.preference_judge import make_caller
spec = sys.argv[1]
try:
    reply = make_caller(spec, max_tokens=2000, pin="none")("You reply with one word.", "Reply with the single word OK.")
except Exception as exc:
    print(f"FATAL: preflight failed for {spec}: {type(exc).__name__}: {exc}", file=sys.stderr); raise SystemExit(1)
print(f"preflight OK {spec}: {reply.strip()[:40]!r}")
PY
[ $? -eq 0 ] || exit 1

R=experiments/agent5/runs/agent5_w2PsuperstrongNsuperstrong_affBothT1fail_hzRafaelStrong3HelenaProbe3DmHint2Strict_conc_
RUNS=()
for m in $MODELS; do
  for s in $SEEDS; do
    # _s${s}_ keeps s1 from also matching s10..s19; the judge skips anything not there
    n=$(ls -d ${R}${m}_s${s}_*/ 2>/dev/null | wc -l)
    [ "$n" -eq 0 ] && { echo "FATAL: no run dir for ${m} s${s}" >&2; exit 1; }
    RUNS+=("${R}${m}_s${s}_*")
  done
done
echo "[$(date +%H:%M:%S)] ${#RUNS[@]} run globs: $MODELS x seeds $SEEDS"
mkdir -p "$OUT"
rc_all=0
for v in $VARIANTS; do
  for pass in 1 2; do
    echo "[$(date +%H:%M:%S)] detection $CRITIC $v pass $pass -> $OUT"
    "$PROJECT/.venv/bin/python" -m experiments.agent5.detection_judge5 --runs "${RUNS[@]}" \
      --critic "$CRITIC" --judge "$JUDGE" --pin-provider none \
      --out "$OUT" --variant "$v" --run-workers 1 --workers "$WORKERS"
    rc=$?; echo "[$(date +%H:%M:%S)] $v pass $pass rc=$rc"
    [ $rc -eq 0 ] && break          # pass 2 only runs if pass 1 left failed seats
    rc_all=$rc
  done
done
echo "[$(date +%H:%M:%S)] ALL DONE rc=$rc_all"
exit $rc_all
