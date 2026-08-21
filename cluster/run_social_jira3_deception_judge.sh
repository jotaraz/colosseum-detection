#!/bin/bash
# social_jira3 DECEPTION sweep — offline judging of the finished vLLM trees.
#
#   usage: run_social_jira3_deception_judge.sh [shard_i shard_n]
#
# TWO PASSES, IN ORDER, ON ONE JUDGE (see social_jira3_allow_forbid_harmless_plan.md §4):
#   1. L1 turn judge  -> judge_results.json            (full taxonomy; analysed in its own right)
#   2. L2 fabrication -> judge_l2_fabrication_executed.json
# L2 is a PRECISION pass: it re-judges only the turns L1 flagged as "Fabrication (executed)"
# (judge.py:581), so it cannot run before L1 and the order here is mandatory, not stylistic.
# No --summaries pass and no L2 hallucination pass: this study scores fabrication only.
#
# JUDGE = deepseek/deepseek-v4-flash-0731 via OpenRouter. NOT the azure gpt-5.4 the older sj3
# trees were judged with, and NOT deepseek-v4-pro. flash-0731 scores ~57% ABOVE v4-pro on
# identical transcripts (measured on 80 frozen sj4 rr10 rollouts), so numbers from this pass are
# comparable ONLY with each other — never pool them with any pre-existing sj3 or sj4 figure.
#
# EXPLICIT TIMESTAMPED ROOTS, not the parent dirs. `social_jira3_deception_base_qwen` holds TWO
# trees: 20260806-053225 (the real one) and 20260806-053242, an orphan from a duplicate submit
# that ran the same config concurrently. Pointing the judge at the parent would score the orphan
# too and double-count those rollouts. Delete the orphan when convenient; until then, this list
# is the safeguard.
#
# CPU-only: the judge is pure API wall-clock, no GPU. Submit with:
#   condor_submit_bid 100 cluster/run_social_jira3_deception_judge.sub
export HOME="${HOME:-/home/jtaraz}"
set -euo pipefail
export PYTHONUNBUFFERED=1

SHARD_I="${1:-0}"; SHARD_N="${2:-1}"

PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT/experiments/social_jira3"

# OpenRouter creds only. Deliberately NOT syco-bench/.env: that sets AZURE_OPENAI_ENDPOINT and
# would flip provider="auto" callers over to Azure.
set -a; source "$PROJECT/.env"; set +a
[ -n "${OPENROUTER_API_KEY:-}" ] || { echo "FATAL: OPENROUTER_API_KEY unset" >&2; exit 1; }

PY="$PROJECT/.venv/bin/python"
JUDGE_MODEL="deepseek/deepseek-v4-flash-0731"

# judge.py does a BARE `from openrouter_client import OpenRouterClient` (judge.py:392), lazily,
# inside the openrouter branch only — which is why the older azure judge jobs never needed this
# and why the first submit of this job died with ModuleNotFoundError after printing "PASS 1/2".
# The module lives in experiments/social_jira2/, so that directory must be importable. $PROJECT
# is added too (sj4's judge jobs do the same) for the `experiments.*` package imports.
export PYTHONPATH="$PROJECT/experiments/social_jira2:$PROJECT${PYTHONPATH:+:$PYTHONPATH}"
"$PY" -c "from openrouter_client import OpenRouterClient; print('[judge] openrouter_client import OK')"

ROOTS=(
  outputs/social_jira3_deception_base_gptoss/20260806-053225       # 48 runs
  outputs/social_jira3_deception_harmless_gptoss/20260806-085923   # 30 runs
  outputs/social_jira3_deception_base_qwen/20260806-053225         # 48 runs (NOT ...-053242)
)

echo "[judge] host=$(hostname) shard=$SHARD_I/$SHARD_N model=$JUDGE_MODEL"
echo "[judge] roots: ${ROOTS[*]}"
for r in "${ROOTS[@]}"; do
    [ -d "$r" ] || { echo "FATAL: missing root $r" >&2; exit 1; }
    echo "[judge]   $r -> $(find "$r" -name metrics.json | wc -l) runs"
done

echo "[judge] === PASS 1/2: L1 turn judge (full taxonomy) ==="
"$PY" -u judge.py "${ROOTS[@]}" \
  --provider openrouter --model "$JUDGE_MODEL" \
  --shard "$SHARD_I/$SHARD_N" --skip-existing --workers 10 --max-retries 8

echo "[judge] === PASS 2/2: L2 fabrication (precision, on L1's flagged turns) ==="
"$PY" -u judge.py "${ROOTS[@]}" \
  --level2 --phenomenon "Fabrication (executed)" \
  --provider openrouter --model "$JUDGE_MODEL" \
  --shard "$SHARD_I/$SHARD_N" --skip-existing --workers 10 --max-retries 8

echo "[judge] done"
