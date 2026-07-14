#!/bin/bash
# CPU-only HTCondor job: run ONE shard of the social_jira3 turn-judge over the SECOND batch of trees
# (glm decoy-ON, glm nodecoy, qwen nodecoy). Same mechanics as run_social_jira3_judge_shard.sh; the
# completion guard (metrics.json) + --skip-existing mean it only judges finished leaves and is
# idempotent, so re-submitting later tops up any stragglers that finish after this run.
#
# args: $1 = shard index (0-based)  $2 = shard count N  [remaining args passed through to judge.py]
set -eo pipefail
export HOME="${HOME:-/home/jtaraz}"
export PYTHONUNBUFFERED=1

SHARD_I="${1:?shard index}"; SHARD_N="${2:?shard count}"; shift 2

PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT/experiments/social_jira3"
set -a; source /fast/jtaraz/syco-bench/.env; set +a

PY="$PROJECT/.venv/bin/python"
T1=outputs/social_jira3_c2p2_glm_4_7_flash_conflict_quit23_v5_confsweep
T2=outputs/social_jira3_c2p2_glm_4_7_flash_conflict_quit23_v5_confsweep_nodecoy
T3=outputs/social_jira3_c2p2_qwen3_6_35b_a3b_conflict_quit23_v5_confsweep_nodecoy

echo "[judge-shard2] host=$(hostname) shard=$SHARD_I/$SHARD_N extra='$*'"
echo "[judge-shard2] https_proxy=${https_proxy:-UNSET}"

exec "$PY" -u judge.py "$T1" "$T2" "$T3" --shard "$SHARD_I/$SHARD_N" "$@"
