#!/bin/bash
# agent5 DISTRUST or DETECTION judge (distrust_judge5 / detection_judge5) on a CPU compute node,
# both variants in a chain (reasoning, no_reasoning), resuming whatever is already in the out dir.
# Written 2026-09-13 to take over a laptop run over 5.e.viii deepseek + glm53flash s0-4; the
# laptop's partial rows / seat files were rsynced into the out dirs first.
#
#   condor_submit_bid 15 cluster/run_agent5_dd_judge.sub judge=distrust
#   condor_submit_bid 15 cluster/run_agent5_dd_judge.sub judge=detection
#   condor_submit_bid 15 cluster/run_agent5_dd_judge.sub judge=distrust_v2|distrust_v3   (message-level critic, one pass)
export HOME="${HOME:-/home/jtaraz}"
set -uo pipefail
export PYTHONUNBUFFERED=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"
JUDGE="${1:?judge: distrust|detection}"

# Retry the key load (transient NFS "transport endpoint shutdown", see run_agent5_disc_judge.sh).
for attempt in 1 2 3 4 5 6; do
  export BIFROST_API_KEY="$(tr -d ' \r\n' < "$PROJECT/.env2" 2>/dev/null)"
  [ -n "${BIFROST_API_KEY:-}" ] && break
  echo "key load attempt $attempt failed, retrying in 20s" >&2; sleep 20
done
[ -n "${BIFROST_API_KEY:-}" ] || { echo "FATAL: BIFROST_API_KEY unset (.env2)" >&2; exit 1; }
# bifrost is internal: must bypass condor's HTTP proxy.
export NO_PROXY="127.0.0.1,localhost,.is.localnet,is.localnet${NO_PROXY:+,$NO_PROXY}"
export no_proxy="$NO_PROXY"

"$PROJECT/.venv/bin/python" - <<'PY'
import sys
sys.path.insert(0, "/fast/jtaraz/LIARS/colosseum-detection")
from experiments.agent5.preference_judge import make_caller
try:
    reply = make_caller("bifrost:azure/gpt-5.5", max_tokens=2000)("You reply with one word.", "Reply with the single word OK.")
except Exception as exc:
    print(f"FATAL: preflight failed: {type(exc).__name__}: {exc}", file=sys.stderr); raise SystemExit(1)
print(f"preflight OK: {reply.strip()[:40]!r}")
PY
[ $? -eq 0 ] || exit 1

R=experiments/agent5/runs/agent5_w2PsuperstrongNsuperstrong_affBothT1fail_hzRafaelStrong3HelenaProbe3DmHint2Strict_conc_
RUNS=("${R}glm53flash_s[0-4]_*" "${R}deepseek_s[0-4]_*")
if [ "$JUDGE" = distrust_v2 ] || [ "$JUDGE" = distrust_v3 ]; then
  VER="${JUDGE#distrust_}"
  echo "[$(date +%H:%M:%S)] distrust $VER"
  "$PROJECT/.venv/bin/python" -m experiments.agent5.distrust_judge5 --runs "${RUNS[@]}" \
    --critic "$VER" --out "experiments/agent5/outputs/distrust_5e8_$VER" --workers 4 --resume
  rc=$?; echo "[$(date +%H:%M:%S)] ALL DONE rc=$rc"; exit $rc
fi
rc_all=0
for v in reasoning no_reasoning; do
  echo "[$(date +%H:%M:%S)] $JUDGE $v"
  if [ "$JUDGE" = distrust ]; then
    "$PROJECT/.venv/bin/python" -m experiments.agent5.distrust_judge5 --runs "${RUNS[@]}" \
      --critic v1 --out experiments/agent5/outputs/distrust_5e8_v1 --variant "$v" --workers 3 --resume
  else
    "$PROJECT/.venv/bin/python" -m experiments.agent5.detection_judge5 --runs "${RUNS[@]}" \
      --out experiments/agent5/outputs/detection_5e8_v1 --variant "$v" --run-workers 2 --workers 1
  fi
  rc=$?; echo "[$(date +%H:%M:%S)] $JUDGE $v rc=$rc"; [ $rc -eq 0 ] || rc_all=$rc
done
echo "[$(date +%H:%M:%S)] ALL DONE rc=$rc_all"
exit $rc_all
