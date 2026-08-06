#!/bin/bash
# social_jira4 — score already-written rollouts with rejudge.py. NOTHING is generated or re-rolled.
#
#   usage: run_sj4_rejudge.sh <rejudge.py flags...>
#
# All arguments are passed through VERBATIM, as in run_sj4_fixedprompt.sh.
#
# CPU-ONLY, and cheap to place: no GPUs, no HF cache, no CUDA module, no vLLM. The rollouts are
# already on disk; every call this makes is an HTTP request to the judge over the proxy. It reads
# run dirs and writes only under the judgments tree — a rollout is never modified, so this is safe
# to run against cells whose generation jobs are still in flight. Cells are re-read from disk each
# pass and resume is per rollout, so re-running as more cells land only judges what is new.
#
# WHY IT RUNS HERE RATHER THAN ON A LAPTOP. The rollouts live on /fast. Judging them locally means
# first pulling hundreds of MB down; judging them here is the same API spend with none of the copy,
# and the credentials are already in place.
#
# CREDENTIALS: OpenRouter only, from the repo .env. No Azure — sourcing /fast/jtaraz/syco-bench/.env
# would set AZURE_OPENAI_ENDPOINT and flip every provider="auto" caller in llm.py over to Azure,
# silently moving the judge off the model this study is defined by.
#
# Uses the plain `.venv` — the vLLM overlay is only needed to SERVE models locally.
set -euo pipefail
export PYTHONUNBUFFERED=1
export HOME="${HOME:-/home/jtaraz}"
export PATH="$HOME/.local/bin:$PATH"
case ":$PATH:" in *":/usr/bin:"*) ;; *) export PATH="$PATH:/usr/bin:/bin";; esac

PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"
source .venv/bin/activate
export PYTHONPATH="$PROJECT"
export TMPDIR=/fast/jtaraz/tmp; mkdir -p "$TMPDIR"

set -a; source "$PROJECT/.env" 2>/dev/null || true; set +a

# Bound the retry storm: a wedged judge call blocks one worker in the pool, not the job. A call
# that still fails after the retries lands in verdicts.jsonl as unparsed rather than as a silent
# zero, and a later pass with --force can redo it.
export OPENROUTER_MAX_RETRIES="${OPENROUTER_MAX_RETRIES:-3}"

echo "node=$(hostname) proxy=${https_proxy:-none} start=$(date) git=$(git rev-parse --short HEAD)"
echo "args=$*"
echo "openrouter_key=$([ -n "${OPENROUTER_API_KEY:-}" ] && echo set || echo MISSING)"
echo "azure_endpoint=${AZURE_OPENAI_ENDPOINT:-unset (correct — the judge must stay on OpenRouter)}"

exec python -u -m experiments.social_jira4.rejudge "$@"
