#!/bin/bash
# agent5 DISCLOSURE judge (disclosure_judge5, critic v2) on a CPU compute node, as a chain of
# slices that share one output directory per cell via --resume. Written 2026-09-13 to take over
# a laptop run of 5.e.viii + 5.e.x (deepseek + glm53flash) so the laptop can go offline; the
# partial rows the laptop had were rsynced into the out dir first.
#
#   condor_submit_bid 15 cluster/run_agent5_disc_judge.sub workers=3 \
#       out="experiments/agent5/outputs/disclosure_5e11_v2" \
#       globs="experiments/agent5/runs/*ConfNone_conc_deepseek_s* experiments/agent5/runs/*ConfNone_conc_glm53flash_s*"
#
# workers = per-judge concurrency (the two judges run side by side). Every glob is one slice
# into the same out dir via --resume, so the glob list can be one entry or many.
export HOME="${HOME:-/home/jtaraz}"
set -uo pipefail
export PYTHONUNBUFFERED=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"
WORKERS="${1:-3}"
OUT="${2:?out dir required}"
EXTRA="${3:-}"          # pass-through flags for disclosure_judge5, e.g. "--pin-provider Parasail"
shift 3
GLOBS=("$@")
[ ${#GLOBS[@]} -gt 0 ] || { echo "FATAL: no run globs given" >&2; exit 1; }

# Retry the key load: job 17553688 died on a transient NFS "Cannot send after transport
# endpoint shutdown" reading .env on the compute node (2026-09-13, first occurrence ever).
for attempt in 1 2 3 4 5 6; do
  set -a; source "$PROJECT/.env" 2>/dev/null; set +a
  export BIFROST_API_KEY="$(tr -d ' \r\n' < "$PROJECT/.env2" 2>/dev/null)"
  [ -n "${OPENROUTER_API_KEY:-}" ] && [ -n "${BIFROST_API_KEY:-}" ] && break
  echo "key load attempt $attempt failed, retrying in 20s" >&2; sleep 20
done
[ -n "${OPENROUTER_API_KEY:-}" ] || { echo "FATAL: OPENROUTER_API_KEY unset" >&2; exit 1; }
[ -n "${BIFROST_API_KEY:-}" ]    || { echo "FATAL: BIFROST_API_KEY unset (.env2)" >&2; exit 1; }
# bifrost is internal: must bypass condor's HTTP proxy (see run_agent5_pref_judge.sh).
export NO_PROXY="127.0.0.1,localhost,.is.localnet,is.localnet${NO_PROXY:+,$NO_PROXY}"
export no_proxy="$NO_PROXY"

"$PROJECT/.venv/bin/python" - <<'PY'
import sys
sys.path.insert(0, "/fast/jtaraz/LIARS/colosseum-detection")
from experiments.agent5.preference_judge import make_caller
for spec in ("bifrost:azure/gpt-5.5", "openrouter:deepseek/deepseek-v4-flash-0731"):
    try:
        reply = make_caller(spec, max_tokens=2000)("You reply with one word.", "Reply with the single word OK.")
    except Exception as exc:
        print(f"FATAL: preflight failed for {spec}: {type(exc).__name__}: {exc}", file=sys.stderr); raise SystemExit(1)
    print(f"preflight OK {spec}: {reply.strip()[:40]!r}")
PY

mkdir -p "$OUT"
for runs in "${GLOBS[@]}"; do
  echo "[$(date +%H:%M:%S)] SLICE $runs -> $OUT"
  "$PROJECT/.venv/bin/python" -m experiments.agent5.disclosure_judge5 \
    --runs "$runs" --out "$OUT" --workers "$WORKERS" --resume $EXTRA
  echo "[$(date +%H:%M:%S)] slice rc=$?"
done
echo "[$(date +%H:%M:%S)] ALL DONE"
