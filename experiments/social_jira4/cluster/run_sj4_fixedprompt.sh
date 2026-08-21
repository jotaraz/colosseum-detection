#!/bin/bash
# social_jira4 — replay already-written prompts against a target model (fixed_prompt_run.py).
#
#   usage: run_sj4_fixedprompt.sh <fixed_prompt_run.py flags...>
#
# All arguments are passed through VERBATIM. This used to take three positionals
# (<step-file> <config> <out-dir>) back when fixed_prompt_run.py replayed one prompt at one seed
# and fanned out over models; it now takes --step-files / --seeds / --models and fans out over
# (prompt x seed) with one model per job, so parsing positionals here would only constrain what the
# submit file can express. The older fp_*.sub files use the retired positional form and would need
# updating before being re-run — their results are already collected.
#
# CPU-ONLY. Nothing runs on vLLM, so this needs no GPUs, no HF cache, no CUDA module, no
# venv-vllm023 and none of the server-startup machinery in run_sj4_metagate.sh — every model call
# leaves the node over the HTTP proxy. That also means no `no_proxy` export: the localhost bypass
# exists purely so a vLLM readiness check can reach a server on 127.0.0.1, and there is none here.
#
# Uses the plain `.venv` (the vLLM 0.23 overlay is only needed to SERVE models locally; the
# OpenRouter client is just `requests`).
#
# CREDENTIALS: OpenRouter only, from the repo .env — it pays for both the assistant rollouts and
# the deepseek-v4-pro critics. No Azure: there is no prompter and no gpt-5.4 meta-judge in these
# runs, so /fast/jtaraz/syco-bench/.env is deliberately NOT sourced. Sourcing it would set
# AZURE_OPENAI_ENDPOINT and flip every provider="auto" caller in llm.py over to Azure, silently
# moving the critics off deepseek and breaking comparability.
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

# Explicit, rather than left to OpenRouterClient's load_dotenv() — that walks up from the CWD and
# would find .env here by accident of where we cd'd, which is not a property to depend on.
set -a; source "$PROJECT/.env" 2>/dev/null || true; set +a

# BOUND THE RETRY STORM. Rollouts fan out inside the process, so a wedged call blocks one worker
# rather than the whole job — but the client's default of 6 retries against a 420s total_timeout is
# still ~42 minutes of one thread doing nothing. Three caps that at ~21 minutes while leaving room
# for a genuinely slow-but-alive call (deepseek-v4-flash on Morph medians 84s). A call that still
# fails errors that ONE (prompt, seed) cell and the run continues; results.json is rewritten after
# every cell, so nothing already computed is lost.
export OPENROUTER_MAX_RETRIES="${OPENROUTER_MAX_RETRIES:-3}"

echo "node=$(hostname) proxy=${https_proxy:-none} start=$(date) git=$(git rev-parse --short HEAD)"
echo "args=$*"
echo "openrouter_key=$([ -n "${OPENROUTER_API_KEY:-}" ] && echo set || echo MISSING)"
echo "azure_endpoint=${AZURE_OPENAI_ENDPOINT:-unset (correct — critics must stay on deepseek)}"

exec python -u -m experiments.social_jira4.fixed_prompt_run "$@"
