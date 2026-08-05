#!/bin/bash
# social_jira4 — the rationale-first meta-judge panel over the 52 qualifying v4 prompts.
#
#   usage: run_mj_sweep_v4.sh [extra meta_judge_sweep.py flags...]
#
# Five questions (META_JUDGE_1..5_RATIONALE_FIRST) x four judge models x 52 prompts = 1040 calls.
# Trailing flags are appended verbatim, so a rerun can narrow the panel with e.g. `--seats gpt54`
# without editing this file.
#
# THE SWEEP AND THE MERGE ARE ONE JOB. The sweep appends to the sidecar
# (reports/v4_dspro_meta_judge_verdicts.jsonl) as verdicts land; the merge then folds them into
# reports/v4_dspro_fabrication_qualifying.jsonl. Both are idempotent — the sweep skips what already
# parsed, the merge rewrites the whole block from the sidecar — so a resubmit after a partial run
# patches the failures and re-merges rather than duplicating anything. The merge runs even if the
# sweep exits non-zero (hence no `set -e` around it): a run that failed 30 calls should still leave
# the other 1010 merged and readable.
#
# CPU-only: every call leaves the node over the HTTP proxy.
#
# Credentials come from two places, on purpose:
#   * OpenRouter — the repo .env (dspro / dsflash / sonnet5 seats).
#   * Azure      — /fast/jtaraz/syco-bench/.env, the only place on this cluster holding
#                  AZURE_OPENAI_*; sj3's Azure path reads os.environ directly and never calls
#                  load_dotenv, so it has to be exported here or the gpt-5.4 seat dies on its
#                  first call.
# Sourcing that file also sets AZURE_OPENAI_ENDPOINT, which flips any provider="auto" caller in
# llm.py over to Azure. Harmless here — every seat in PANEL names its provider explicitly.
set -uo pipefail
export PYTHONUNBUFFERED=1
export HOME="${HOME:-/home/jtaraz}"
export PATH="$HOME/.local/bin:$PATH"
case ":$PATH:" in *":/usr/bin:"*) ;; *) export PATH="$PATH:/usr/bin:/bin";; esac

PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"
source .venv/bin/activate
export PYTHONPATH="$PROJECT"
export TMPDIR=/fast/jtaraz/tmp; mkdir -p "$TMPDIR"

# Explicit, rather than left to OpenRouterClient's load_dotenv() walking up from the CWD.
set -a; source "$PROJECT/.env" 2>/dev/null || true; set +a

AZURE_ENV=/fast/jtaraz/syco-bench/.env
if [ -f "$AZURE_ENV" ]; then
    set -a; source "$AZURE_ENV"; set +a
else
    echo "WARNING: $AZURE_ENV not found — the gpt-5.4 seat will fail" >&2
fi

# Bound the retry storm: three OpenRouter seats are PINNED with allow_fallbacks=false, so a
# provider outage is a hard 404 rather than a reroute, and there is no point spending the client's
# default 6 retries against a wall.
export OPENROUTER_MAX_RETRIES="${OPENROUTER_MAX_RETRIES:-3}"

echo "node=$(hostname) proxy=${https_proxy:-none} start=$(date) git=$(git rev-parse --short HEAD)"
echo "extra=$*"
echo "openrouter_key=$([ -n "${OPENROUTER_API_KEY:-}" ] && echo set || echo MISSING)"
echo "azure_endpoint=${AZURE_OPENAI_ENDPOINT:-unset} azure_deployment=${AZURE_JUDGE_DEPLOYMENT:-gpt-5.4 (default)}"
echo "azure_key=$([ -n "${AZURE_OPENAI_API_KEY:-}" ] && echo set || echo MISSING)"

python -u -m experiments.social_jira4.meta_judge_sweep "$@"
SWEEP_RC=$?
echo "sweep exited rc=$SWEEP_RC at $(date); merging what landed"

python -u -m experiments.social_jira4.meta_judge_sweep --merge
MERGE_RC=$?
echo "merge exited rc=$MERGE_RC at $(date)"

exit $SWEEP_RC
