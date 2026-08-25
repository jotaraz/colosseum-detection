#!/bin/bash
# agent3 — batched ask optimization on v15 / deepseek-v4-flash-0731, on a COMPUTE node.
#
# Every call here is OpenRouter over the execute node's HTTPS proxy. Nothing runs on the login
# node: a step is ~25 minutes of waiting on a hosted API and a 20-step run is ~10 hours.
#
#   condor_submit_bid 100 cluster/run_agent3_wire.sub   # 1 step x 1 replicate, ~20 min
#   condor_submit_bid 100 cluster/run_agent3_v15.sub    # the real run
#
# Arguments (all optional, in this order):
#   $1 out-dir name under experiments/agent3/outputs   (default: run02)
#   $2 total steps                                     (default: 20)
#   $3 replicates per candidate (M)                    (default: 3)
#   $4 warm-start arms, comma separated                (default: askA,askK,askG)
#   $5 reward to climb: v1 | v2                        (default: v1)
#   $6 judge workers per rollout                       (default: 6 — lower it when two runs
#                                                       share the account, so their judging
#                                                       bursts do not collide)
#   $7 prompter model                                  (default: z-ai/glm-5.3)
#   $8 warm-targeted: 1 to read arms' existing lie_* files instead of requiring a full sweep
#                                                       (default: 0)
#   $9 warm-prior: comma-separated prior out-dirs whose own candidates, re-judged under this
#      reward's judge, become extra warm entries (default: none)
#
# **Resumes by itself.** If the out-dir already holds a run, `--resume` is passed and `--steps`
# is read as the TOTAL, so an evicted or re-queued job continues from its last completed step
# rather than starting over or refusing to run. Step files are written only after a batch is
# fully judged, so nothing half-finished is ever resumed from.
set -uo pipefail
export HOME="${HOME:-/home/jtaraz}"
export PYTHONUNBUFFERED=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT" || exit 1
export PYTHONPATH="$PROJECT"
set -a; source "$PROJECT/.env"; set +a

RUN="${1:-run02}"
STEPS="${2:-20}"
REPS="${3:-3}"
ARMS="${4:-askA,askK,askG}"
REWARD="${5:-v1}"
JUDGE_WORKERS="${6:-6}"
PROMPTER="${7:-z-ai/glm-5.3}"
WARM_TARGETED="${8:-0}"
WARM_PRIOR="${9:-}"
# Every rollout of a batch in one pool: 3 candidates x M replicates. Left at the default 9 a
# batch at M=4 would run in two waves and the step would cost twice the slowest rollout.
PARALLEL=$(( 3 * REPS ))
OUT="experiments/agent3/outputs/${RUN}"

# The client retries transport errors itself; 6 (its default) means a stalled upstream can hold
# a rollout for the better part of an hour, and with 9 rollouts in flight one stall gates the
# whole step. 2 fails faster and the loop treats a dead rollout as one lost replicate, not a
# lost candidate.
export OPENROUTER_MAX_RETRIES="${OPENROUTER_MAX_RETRIES:-2}"

echo "[$(date +%H:%M:%S)] host=$(hostname) proxy=${https_proxy:-<none>}"
echo "[$(date +%H:%M:%S)] run=$RUN steps=$STEPS replicates=$REPS parallel=$PARALLEL arms=$ARMS reward=$REWARD judge_workers=$JUDGE_WORKERS prompter=$PROMPTER warm_targeted=$WARM_TARGETED warm_prior=$WARM_PRIOR"

# ---- fail before spending anything -------------------------------------------------------
[ -n "${OPENROUTER_API_KEY:-}" ] || { echo "FATAL: OPENROUTER_API_KEY unset" >&2; exit 1; }
[ -f "$PROJECT/experiments/agent1/fixtures/aug2026_v15_renamed.json" ] \
  || { echo "FATAL: v15 fixture not deployed" >&2; exit 1; }
[ -f "$PROJECT/experiments/agent3/configs/agent3_v15_deepseek.yaml" ] \
  || { echo "FATAL: target config not deployed" >&2; exit 1; }

# Pure checks (no network): majority, reward, batch parsing, both tool adapters.
"$PROJECT/.venv/bin/python" -m experiments.agent3.smoke || { echo "FATAL: smoke failed" >&2; exit 1; }

# Does this node actually reach OpenRouter through the proxy? One cheap call, before the run
# commits to hours of them. A compute node with no egress otherwise fails 9 rollouts deep.
"$PROJECT/.venv/bin/python" - <<'PY' || { echo "FATAL: no OpenRouter egress from this node" >&2; exit 1; }
import sys
from experiments.social_jira2.openrouter_client import OpenRouterClient
c = OpenRouterClient(request_timeout=120, total_timeout=180)
data, text = c.generate_response(
    OpenRouterClient.init_context("Reply with the single word OK.", "Go."),
    {"model": "deepseek/deepseek-v4-flash-0731", "max_completion_tokens": 2000,
     "temperature": 0, "provider": {"order": ["GMICloud"], "allow_fallbacks": False}})
print("  egress OK — provider=%s, %s prompt tokens" % (
    data.get("provider"), (data.get("usage") or {}).get("prompt_tokens")))
sys.exit(0 if data.get("provider") else 1)
PY

# ---- the run ------------------------------------------------------------------------------
RESUME=""
if [ -f "$OUT/history.jsonl" ]; then
  RESUME="--resume"
  echo "[$(date +%H:%M:%S)] $OUT exists — resuming (steps=$STEPS is the TOTAL)"
fi

"$PROJECT/.venv/bin/python" -m experiments.agent3.loop \
    --steps "$STEPS" --replicates "$REPS" $RESUME \
    --config experiments/agent3/configs/agent3_v15_deepseek.yaml \
    --warm-start "$ARMS" --reward "$REWARD" --judge-workers "$JUDGE_WORKERS" \
    --prompter-model "$PROMPTER" --parallel-rollouts "$PARALLEL" \
    $([ "$WARM_TARGETED" = "1" ] && echo --warm-targeted) \
    $([ -n "$WARM_PRIOR" ] && echo --warm-prior "$WARM_PRIOR") \
    --out-dir "$OUT" \
    2>&1 | tee -a "$PROJECT/logs/agent3_${RUN}.log"
rc=${PIPESTATUS[0]}
echo "[$(date +%H:%M:%S)] loop exited rc=$rc"

echo "=== per-step summary ==="
"$PROJECT/.venv/bin/python" -m experiments.agent3.progress "$OUT" 2>&1 | tail -60
exit "$rc"
