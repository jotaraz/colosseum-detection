#!/bin/bash
# CPU-only HTCondor job: LOW-CONCURRENCY repair pass (A). Re-judges only the turns that previously
# failed (parse_error, ~all exhausted-429s) across every already-judged v5_quit23 leaf, and merges
# the fresh judgments back in. Deliberately a SINGLE process at low --workers: the earlier 52% turn
# failure was caused by 128-192 concurrent calls saturating the Azure deployment's rate limit, so
# here we keep aggregate concurrency small (calls then return in ~7s with 0x 429). Idempotent —
# re-running only re-touches turns that are still failed.
#
# args passed straight through to judge.py (e.g. --workers 8 --max-retries 20)
set -eo pipefail
export HOME="${HOME:-/home/jtaraz}"
export PYTHONUNBUFFERED=1

PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT/experiments/social_jira3"
set -a; source /fast/jtaraz/syco-bench/.env; set +a
PY="$PROJECT/.venv/bin/python"

TREES="outputs/social_jira3_c2p2_gptoss_120b_medium_conflict_quit23_v5_confsweep \
outputs/social_jira3_c2p2_gptoss_120b_medium_conflict_quit23_v5_confsweep_nodecoy \
outputs/social_jira3_c2p2_qwen3_6_35b_a3b_conflict_quit23_v5_confsweep \
outputs/social_jira3_c2p2_glm_4_7_flash_conflict_quit23_v5_confsweep \
outputs/social_jira3_c2p2_glm_4_7_flash_conflict_quit23_v5_confsweep_nodecoy \
outputs/social_jira3_c2p2_qwen3_6_35b_a3b_conflict_quit23_v5_confsweep_nodecoy"

echo "[repair] host=$(hostname) args='$*'"
exec "$PY" -u judge.py $TREES --repair "$@"
