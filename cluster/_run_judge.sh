#!/bin/bash
cd /fast/jtaraz/LIARS/colosseum-detection || exit 1
set -a; source /fast/jtaraz/syco-bench/.env 2>/dev/null; set +a
OUT=experiments/social_jira1/outputs
for eff in low medium high; do
  root=$(ls -d $OUT/social_jira1_n4_gptoss_120b_${eff}/*/ 2>/dev/null | sort | tail -1)
  echo "=== judging $eff : $root ($(date +%H:%M)) ==="
  .venv/bin/python -m experiments.social_jira1.judge_lying --root "$root" --max-concurrent 3 --votes 3
done
echo "ALL JUDGING DONE ($(date +%H:%M))"
