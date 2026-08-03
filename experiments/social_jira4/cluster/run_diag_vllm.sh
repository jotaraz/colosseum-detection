#!/bin/bash
# Diagnostic: bring up the two sj4 target servers on one node and report exactly what happens.
#
#   usage: run_diag_vllm.sh [port-base]        (default 8300)
#
# Same environment, same venv, same config template and the same runtime call path as
# run_sj4_metagate.sh — but from a PRIVATE working directory, so each server's log survives instead
# of being truncated by a neighbouring job, and the tail of it is printed when a server fails.
# That log is the one thing the v4c post-mortem never had.
export HOME="${HOME:-/home/jtaraz}"
set -euo pipefail
export PYTHONUNBUFFERED=1
export PATH="$HOME/.local/bin:$PATH"
case ":$PATH:" in *":/usr/bin:"*) ;; *) export PATH="$PATH:/usr/bin:/bin";; esac
export no_proxy="127.0.0.1,localhost,0.0.0.0,::1"
export NO_PROXY="127.0.0.1,localhost,0.0.0.0,::1"

PORT_BASE="${1:-8300}"
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
VENV="$PROJECT/.venv-vllm023"
export HF_HOME=/fast/jtaraz/hf_cache
export PYTHONPATH="$PROJECT"
export TMPDIR=/fast/jtaraz/tmp; mkdir -p "$TMPDIR"

source "$VENV/bin/activate"
python -c "import vllm; print('vllm', vllm.__version__)"
python "$PROJECT/cluster/patch_vllm_client.py" "$VENV/lib/python3.11/site-packages/llm_server/clients/vllm_client.py" || echo "WARN: vllm_client patch failed"

source /etc/profile.d/modules.sh 2>/dev/null || true
module load cuda/12.9 2>/dev/null || echo "WARNING: module load cuda/12.9 failed"
if ! command -v nvcc >/dev/null 2>&1; then
    export CUDA_HOME=/is/software/nvidia/cuda-13.2
    export PATH="$CUDA_HOME/bin:$PATH"
    export LD_LIBRARY_PATH="$CUDA_HOME/lib64:${LD_LIBRARY_PATH:-}"
fi
export VLLM_USE_FLASHINFER_SAMPLER=0
export VLLM_USE_DEEP_GEMM=0

echo "node=$(hostname) date=$(date)"
echo "nvidia-smi:"; nvidia-smi --query-gpu=index,name,memory.used,memory.total --format=csv,noheader || true
echo "free RAM:"; free -g | head -2 || true
echo "nvcc: $(command -v nvcc || echo MISSING)"
echo "disk on /fast: $(df -h /fast | tail -1)"

ALLOC="${CUDA_VISIBLE_DEVICES:-0,1,2,3}"
IFS=',' read -r -a GPUS <<< "$ALLOC"
if [ "${#GPUS[@]}" -lt 4 ]; then echo "need 4 GPUs, got $ALLOC" >&2; exit 1; fi
QWEN_GPUS="${GPUS[0]},${GPUS[1]}"
GPTOSS_GPUS="${GPUS[2]},${GPUS[3]}"
echo "alloc=$ALLOC qwen=$QWEN_GPUS:$PORT_BASE gptoss=$GPTOSS_GPUS:$((PORT_BASE + 1))"

DIAG="$PROJECT/experiments/social_jira4/outputs/_diag_vllm"
rm -rf "$DIAG"; mkdir -p "$DIAG"
sed -e "s/__QWEN_PORT__/$PORT_BASE/" \
    -e "s/__GPTOSS_PORT__/$((PORT_BASE + 1))/" \
    -e "s/__QWEN_GPUS__/$QWEN_GPUS/" \
    -e "s/__GPTOSS_GPUS__/$GPTOSS_GPUS/" \
    "$PROJECT/experiments/social_jira4/configs/social_jira4_dual_vllm_r4.yaml.tmpl" \
    > "$DIAG/target_config.yaml"

cd "$DIAG"          # private CWD: logs/vllm/<id>.log belongs to this job alone
python "$PROJECT/experiments/social_jira4/cluster/diag_vllm.py" "$DIAG/target_config.yaml"
rc=$?
echo "=== server logs kept at $DIAG/logs/vllm/ ==="
ls -la "$DIAG/logs/vllm/" 2>/dev/null || true
exit $rc
