#!/bin/bash
# CPU-only HTCondor job: run ONE shard of the social_jira3 offline turn-judge on a compute node.
# The judge is I/O-bound (Azure gpt-5.4 HTTP calls) — no GPU. Compute nodes reach Azure via the
# cluster's auto-set http(s)_proxy. Output judge_results.json is written per leaf onto shared /fast.
#
# args: $1 = shard index (0-based)  $2 = shard count N  [remaining args passed through to judge.py]
set -eo pipefail
export HOME="${HOME:-/home/jtaraz}"
export PYTHONUNBUFFERED=1

SHARD_I="${1:?shard index}"; SHARD_N="${2:?shard count}"; shift 2

PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT/experiments/social_jira3"

# Azure judge creds (gpt-5.4). set -a so the sourced vars are exported to python.
set -a; source /fast/jtaraz/syco-bench/.env; set +a

PY="$PROJECT/.venv/bin/python"
T1=outputs/social_jira3_c2p2_gptoss_120b_medium_conflict_quit23_v5_confsweep
T2=outputs/social_jira3_c2p2_gptoss_120b_medium_conflict_quit23_v5_confsweep_nodecoy
T3=outputs/social_jira3_c2p2_qwen3_6_35b_a3b_conflict_quit23_v5_confsweep

echo "[judge-shard] host=$(hostname) shard=$SHARD_I/$SHARD_N extra='$*'"
echo "[judge-shard] https_proxy=${https_proxy:-UNSET} http_proxy=${http_proxy:-UNSET}"
"$PY" -c "import requests,sys;print('[judge-shard] requests',requests.__version__,'py',sys.version.split()[0])"

exec "$PY" -u judge.py "$T1" "$T2" "$T3" --shard "$SHARD_I/$SHARD_N" "$@"
