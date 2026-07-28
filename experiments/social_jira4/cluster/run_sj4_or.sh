#!/bin/bash
# social_jira4 — one adaptive-prompter optimization run against an OpenRouter target, on a
# compute node. CPU-only: every model call (target, prompter, judges, validator, consistency)
# goes out over the node's HTTP proxy, so no GPU and no local weights are involved.
#
#   usage: run_sj4_or.sh <target-config.yaml> <model-label> <out-dir> [extra loop.py flags...]
#
# Defaults below are the v3 run budget (8 optimizer steps x 3 rollout seeds). Trailing flags are
# appended verbatim, and argparse takes the LAST occurrence of a repeated option — so a warm-start
# job passes `--steps 9 --warmstart ...` and gets 9 steps, not 8.
#
# Supersedes run_sj4.sh (which hardcoded its flags); kept as a separate file so submitting new work
# cannot disturb a job already running off the old script.
set -e
cd /fast/jtaraz/LIARS/colosseum-detection
source .venv/bin/activate
export PYTHONPATH=/fast/jtaraz/LIARS/colosseum-detection
export TMPDIR=/fast/jtaraz/tmp; mkdir -p "$TMPDIR"

CONFIG="$1"; LABEL="$2"; OUT="$3"; shift 3

echo "node=$(hostname) proxy=${https_proxy:-none} start=$(date) git=$(git rev-parse --short HEAD)"
echo "config=$CONFIG label=$LABEL out=$OUT extra=$*"

exec python -u -m experiments.social_jira4.loop \
    --mode live \
    --steps 8 \
    --seeds 1,2,3 \
    --config "$CONFIG" \
    --model-label "$LABEL" \
    --out-dir "$OUT" \
    "$@"
