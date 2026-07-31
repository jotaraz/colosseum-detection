#!/bin/bash
set -e
cd /fast/jtaraz/LIARS/colosseum-detection
source .venv/bin/activate
export PYTHONPATH=/fast/jtaraz/LIARS/colosseum-detection
export TMPDIR=/fast/jtaraz/tmp; mkdir -p "$TMPDIR"
echo "node=$(hostname) proxy=${https_proxy:-none} start=$(date)"
exec python -u -m experiments.social_jira4.loop --mode live --steps 8 --seeds 1,2,3 --config "$1" --model-label "$2" --out-dir "$3"
