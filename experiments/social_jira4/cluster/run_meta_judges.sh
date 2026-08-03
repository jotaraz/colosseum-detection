#!/bin/bash
# social_jira4 — the meta-judge panel over the v3 rendered prompts, on a compute node.
#
#   usage: run_meta_judges.sh <out.jsonl> [extra judge_prompts.py flags...]
#
# Six questions (META_JUDGE_1..6, see meta-judges-feeling.md) x two judge models x two views
# (system alone, and system+user) over the 65 rendered prompts that have one. Trailing flags are
# appended verbatim and argparse takes the LAST occurrence of a repeated option, so a rerun can
# narrow the panel with e.g. `--models gpt54` without editing this file.
#
# CPU-only: every call leaves the node over the HTTP proxy.
#
# Credentials come from two places, on purpose:
#   * OpenRouter — the repo .env, which OpenRouterClient load_dotenv()s for itself.
#   * Azure      — /fast/jtaraz/syco-bench/.env, which is where sj3's _azure_chat error message
#                  points and the only place on this cluster holding AZURE_OPENAI_*. sj3's Azure
#                  path reads os.environ directly and never calls load_dotenv, so it has to be
#                  exported here or the gpt-5.4 half of the panel dies on the first call.
# Sourcing that file also sets AZURE_OPENAI_ENDPOINT, which flips any provider="auto" caller in
# llm.py over to Azure. Harmless here — every judge in the panel names its provider explicitly.
set -e
cd /fast/jtaraz/LIARS/colosseum-detection
source .venv/bin/activate
export PYTHONPATH=/fast/jtaraz/LIARS/colosseum-detection
export TMPDIR=/fast/jtaraz/tmp; mkdir -p "$TMPDIR"

AZURE_ENV=/fast/jtaraz/syco-bench/.env
if [ -f "$AZURE_ENV" ]; then
    set -a; source "$AZURE_ENV"; set +a
else
    echo "WARNING: $AZURE_ENV not found — the gpt-5.4 judges will fail" >&2
fi

OUT="$1"; shift

echo "node=$(hostname) proxy=${https_proxy:-none} start=$(date) git=$(git rev-parse --short HEAD)"
echo "out=$OUT extra=$*"
echo "azure_endpoint=${AZURE_OPENAI_ENDPOINT:-unset} azure_deployment=${AZURE_JUDGE_DEPLOYMENT:-unset}"
echo "azure_key=$([ -n "$AZURE_OPENAI_API_KEY" ] && echo set || echo MISSING)"
echo "openrouter_key=$(grep -q OPENROUTER_API_KEY .env && echo "set (repo .env)" || echo MISSING)"

exec python -u -m experiments.social_jira4.judge_prompts \
    --input experiments/social_jira4/reports/v3_prompts_rendered.jsonl \
    --out "$OUT" \
    --workers 8 \
    "$@"
