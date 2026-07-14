#!/bin/bash
# CPU-only LEVEL-2 precision (single-phenomenon confirmation) judges for the v6 seeds3456 tree.
# Runs BOTH phenomena sequentially so peak concurrency stays at --workers 16 (the proven-clean
# single-node config, below the gpt-5.4 429 ceiling):
#   * Fabrication (executed) -> judge_l2_fabrication_executed.json   (~25 L1-flagged turns)
#   * Hallucination          -> judge_l2_hallucination.json          (~82 L1-flagged turns)
# Re-judges ONLY the turns judge_results.json flagged as that phenomenon, with the tightened
# single-phenomenon prompt (L2 is blind to L1's verdict; it can only shrink the flagged set ->
# measures precision). Non-destructive, idempotent/resumable via --skip-existing. I/O-bound Azure
# gpt-5.4 HTTP calls — no GPU; compute nodes reach Azure via the auto-set http(s)_proxy.
#   condor_submit_bid 50 cluster/run_social_jira3_judge_l2_v6_seeds3456.sub
set -eo pipefail
export HOME="${HOME:-/home/jtaraz}"
export PYTHONUNBUFFERED=1

PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT/experiments/social_jira3"

# Azure judge creds (gpt-5.4). set -a so the sourced vars are exported to python.
set -a; source /fast/jtaraz/syco-bench/.env; set +a
PY="$PROJECT/.venv/bin/python"

TREE=outputs/social_jira3_c2p2_qwen3_6_35b_a3b_conflict_quit23_v6_confsweep_seeds3456

echo "[l2-judge] host=$(hostname)"
echo "[l2-judge] https_proxy=${https_proxy:-UNSET} http_proxy=${http_proxy:-UNSET}"
"$PY" -c "import requests,sys;print('[l2-judge] requests',requests.__version__,'py',sys.version.split()[0])"

echo "[l2-judge] === LEVEL-2: Fabrication (executed) ==="
"$PY" -u judge.py "$TREE" \
  --level2 --provider azure --model gpt-5.4 \
  --phenomenon "Fabrication (executed)" \
  --skip-existing --workers 16 --max-retries 8

echo "[l2-judge] === LEVEL-2: Hallucination ==="
"$PY" -u judge.py "$TREE" \
  --level2 --provider azure --model gpt-5.4 \
  --phenomenon "Hallucination" \
  --skip-existing --workers 16 --max-retries 8

echo "[l2-judge] DONE (both phenomena)"
