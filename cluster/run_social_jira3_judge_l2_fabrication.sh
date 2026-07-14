#!/bin/bash
# CPU-only HTCondor job: LEVEL-2 (single-phenomenon confirmation) judge for `Fabrication (executed)`
# over the 5 COMPLETED c2p2_<model>_conflict_quit23_v5_confsweep* trees — all but glm-nodecoy, which
# is only partially judged (49/180). Re-judges ONLY the turns that judge_results.json flagged as
# Fabrication (executed), with the tightened single-phenomenon prompt, and writes
# judge_l2_fabrication_executed.json per leaf (non-destructive). I/O-bound Azure gpt-5.4 HTTP calls —
# no GPU; compute nodes reach Azure via the cluster's auto-set http(s)_proxy. Idempotent/resumable
# via --skip-existing. ~194 flagged turns total, so a single node at --workers 16 is ample.
#
# args: $1 = shard index (0-based, default 0)   $2 = shard count N (default 1)
set -eo pipefail
export HOME="${HOME:-/home/jtaraz}"
export PYTHONUNBUFFERED=1

SHARD_I="${1:-0}"; SHARD_N="${2:-1}"

PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT/experiments/social_jira3"

# Azure judge creds (gpt-5.4). set -a so the sourced vars are exported to python.
set -a; source /fast/jtaraz/syco-bench/.env; set +a

PY="$PROJECT/.venv/bin/python"

# The 5 fully-judged v5_quit23 confsweep trees (glm-nodecoy excluded — not yet fully L1-judged).
GLM_DON=outputs/social_jira3_c2p2_glm_4_7_flash_conflict_quit23_v5_confsweep
GPT_DON=outputs/social_jira3_c2p2_gptoss_120b_medium_conflict_quit23_v5_confsweep
GPT_ND=outputs/social_jira3_c2p2_gptoss_120b_medium_conflict_quit23_v5_confsweep_nodecoy
QWEN_DON=outputs/social_jira3_c2p2_qwen3_6_35b_a3b_conflict_quit23_v5_confsweep
QWEN_ND=outputs/social_jira3_c2p2_qwen3_6_35b_a3b_conflict_quit23_v5_confsweep_nodecoy

echo "[l2-judge] host=$(hostname) shard=$SHARD_I/$SHARD_N"
echo "[l2-judge] https_proxy=${https_proxy:-UNSET} http_proxy=${http_proxy:-UNSET}"
"$PY" -c "import requests,sys;print('[l2-judge] requests',requests.__version__,'py',sys.version.split()[0])"

exec "$PY" -u judge.py \
  "$GLM_DON" "$GPT_DON" "$GPT_ND" "$QWEN_DON" "$QWEN_ND" \
  --level2 --provider azure --model gpt-5.4 \
  --phenomenon "Fabrication (executed)" \
  --shard "$SHARD_I/$SHARD_N" --skip-existing --workers 16 --max-retries 8
