#!/bin/bash
# social_jira4 — replay the consistency gate over stored candidates, on a compute node.
#
#   usage: run_replay_cons.sh <out-dir> [extra replay_consistency.py flags...]
#
# Measures how the gate's verdict depends on whether the judge deliberated: each stored candidate
# is re-asked N times with reasoning_effort unset (the pre-fix behaviour, in which DeepSeek-V4-Pro
# declined to think on ~19% of gate calls) and N times with it set to "medium" (the new
# llm.make_judge_caller default). CPU-only — every call leaves the node over the HTTP proxy.
#
# Defaults replay all six v3-era runs at 10 trials per arm: 43 candidates x 2 arms x 10 = 860
# calls, ~1-2h wall clock at 8 workers. Trailing flags are appended verbatim and argparse takes the
# LAST occurrence of a repeated option, so `--trials 3` from the .sub overrides the default here.
set -e
cd /fast/jtaraz/LIARS/colosseum-detection
source .venv/bin/activate
export PYTHONPATH=/fast/jtaraz/LIARS/colosseum-detection
export TMPDIR=/fast/jtaraz/tmp; mkdir -p "$TMPDIR"

OUT="$1"; shift

echo "node=$(hostname) proxy=${https_proxy:-none} start=$(date) git=$(git rev-parse --short HEAD)"
echo "out=$OUT extra=$*"

exec python -u -m experiments.social_jira4.replay_consistency \
    --out "$OUT" \
    --runs cluster_qwen_r4_v3 cluster_qwen_r4_v3_warm \
           cluster_gptoss_r4_v3 cluster_gptoss_r4_v3_warm \
           cluster_dsflash_r4_v3fix cluster_dsflash_r4_v3fix_warm \
    --trials 10 \
    --efforts "" medium \
    --workers 8 \
    "$@"
