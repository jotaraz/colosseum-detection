#!/bin/bash
# agent5 PREFERENCE judge on a CPU compute node. Two judges per item:
# gpt-5.5 via the institute gateway (bifrost) and deepseek-v4-flash via OpenRouter.
#
#   condor_submit_bid 100 cluster/run_agent5_pref_judge.sub \
#       runs="experiments/agent5/runs/agent5_w1P0N0_*" \
#       out="experiments/agent5/outputs/preference_w1P0N0_v1" workers=8
#
# The cluster is where this runs because gpt-5.5 is reachable ONLY from inside the
# institute network — the laptop cannot see bifrost.is.localnet at all.
export HOME="${HOME:-/home/jtaraz}"
set -euo pipefail
export PYTHONUNBUFFERED=1

PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"

RUNS="${1:?runs glob required}"
OUT="${2:?out dir required}"
WORKERS="${3:-8}"
CLUSTER_ID="${4:-$$}"
# Space-separated judge specs. Overridable so one column can be re-judged on its own: a
# contaminated OpenRouter column is re-run pinned while the gateway column, which has a
# single upstream and cannot be contaminated, is kept and merged in.
JUDGES="${5:-bifrost:azure/gpt-5.5 openrouter:deepseek/deepseek-v4-flash-0731}"
shift 5 2>/dev/null || true

# OPENROUTER_API_KEY from .env; BIFROST_API_KEY is a bare token in .env2 (not KEY=VALUE).
set -a; source "$PROJECT/.env"; set +a
export BIFROST_API_KEY="$(tr -d ' \r\n' < "$PROJECT/.env2")"
[ -n "${OPENROUTER_API_KEY:-}" ] || { echo "FATAL: OPENROUTER_API_KEY unset" >&2; exit 1; }
[ -n "${BIFROST_API_KEY:-}" ]    || { echo "FATAL: BIFROST_API_KEY unset (.env2)" >&2; exit 1; }

# Compute nodes reach the public internet only through condor's HTTP(S) proxy — which
# OpenRouter needs. bifrost is INTERNAL, so it must bypass that proxy or the request is
# routed out of the network and dies; hence is.localnet in NO_PROXY.
export NO_PROXY="127.0.0.1,localhost,.is.localnet,is.localnet${NO_PROXY:+,$NO_PROXY}"
export no_proxy="$NO_PROXY"
echo "proxy: https_proxy=${https_proxy:-unset} no_proxy=$no_proxy"

# Preflight every judge before spending anything on the others: a bifrost that is unreachable
# from this node, or a pinned OpenRouter backend that is down, is a fact worth learning in
# five seconds rather than after half a sweep has been paid for.
JUDGES="$JUDGES" "$PROJECT/.venv/bin/python" - <<'PY'
import os, sys
sys.path.insert(0, "/fast/jtaraz/LIARS/colosseum-detection")
from experiments.agent5.preference_judge import make_caller
for spec in os.environ["JUDGES"].split():
    try:
        reply = make_caller(spec, max_tokens=2000)(
            "You reply with one word.", "Reply with the single word OK.")
    except Exception as exc:
        print(f"FATAL: preflight failed for {spec}: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print(f"preflight OK {spec}: {reply.strip()[:40]!r}")
PY

echo "[$(date +%H:%M:%S)] JUDGING $RUNS -> $OUT (workers=$WORKERS, cluster $CLUSTER_ID)"
echo "judges: $JUDGES"
JUDGE_FLAGS=""
for j in $JUDGES; do JUDGE_FLAGS="$JUDGE_FLAGS --judge $j"; done
# shellcheck disable=SC2086
"$PROJECT/.venv/bin/python" -m experiments.agent5.preference_judge \
  --runs "$RUNS" --out "$OUT" --workers "$WORKERS" $JUDGE_FLAGS "$@"
rc=$?
echo "[$(date +%H:%M:%S)] JUDGE DONE rc=$rc"

# The browsable join, built on the node so the pull-back is one directory. It goes in a
# subdirectory because preference_bundle writes its pooled table to `summary.md` beside the
# bundle — pointed at $OUT directly it silently overwrites the judge's own summary.md, which
# is the richer of the two (four-way tables, confidence spread, inter-judge agreement).
mkdir -p "$OUT/bundle"
"$PROJECT/.venv/bin/python" -m experiments.agent5.preference_bundle \
  --run "j1=$OUT" --out "$OUT/bundle/bundle.html" || echo "bundle failed (verdicts are in $OUT)"
echo "[$(date +%H:%M:%S)] ALL DONE"
