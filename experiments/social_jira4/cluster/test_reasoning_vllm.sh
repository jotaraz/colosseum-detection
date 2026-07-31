#!/bin/bash
# Brief CoT-capture probe: serve Qwen3.6-35B-A3B on local vLLM, then run test_reasoning_vllm.py
# (two calls: plain + tool-call). NOT the full pipeline. Mirrors run_sj4_vllm.sh's serve block.
set -euo pipefail
REPO=/fast/jtaraz/LIARS/colosseum-detection
cd "$REPO"

set -a; [ -f .env ] && . ./.env; set +a
export no_proxy="127.0.0.1,localhost,${no_proxy:-}"
export NO_PROXY="127.0.0.1,localhost,${NO_PROXY:-}"
export TMPDIR=/fast/jtaraz/tmp; mkdir -p "$TMPDIR"
export HF_HOME=/fast/jtaraz/hf_cache          # COMPLETE snapshot (incl. image/video processor configs)
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
# CUDA build toolchain for runtime Triton/flashinfer kernels (Qwen3.6 has GDN linear-attn layers
# that JIT-compile at load). /usr/bin -> ld (job PATH otherwise lacks it); cuda-13.2 -> nvcc/ptxas
# (matches torch 2.11+cu130). Without this, engine core init dies on "cannot find 'ld'".
export PATH=/usr/bin:/is/software/nvidia/cuda-13.2/bin:$PATH
export CUDA_HOME=/is/software/nvidia/cuda-13.2
export LD_LIBRARY_PATH=/is/software/nvidia/cuda-13.2/lib64:${LD_LIBRARY_PATH:-}

VLLM_TP="${VLLM_TP:-2}"
VLLM_LOG="$REPO/experiments/social_jira4/cluster/vllm_test_reasoning.log"
echo "node=$(hostname) gpus=${CUDA_VISIBLE_DEVICES:-?} tp=$VLLM_TP start=$(date)"

"$REPO/.venv-vllm023/bin/python" -m vllm.entrypoints.openai.api_server \
  --model "Qwen/Qwen3.6-35B-A3B" --served-model-name "Qwen/Qwen3.6-35B-A3B" \
  --host 127.0.0.1 --port 8000 \
  --tensor-parallel-size "$VLLM_TP" \
  --gpu-memory-utilization 0.92 \
  --max-model-len 32768 \
  --enable-auto-tool-choice --tool-call-parser hermes \
  --reasoning-parser qwen3 \
  --enforce-eager \
  --no-enable-log-requests > "$VLLM_LOG" 2>&1 &
VLLM_PID=$!
trap 'kill $VLLM_PID 2>/dev/null || true' EXIT

echo "waiting for vLLM /health (log: $VLLM_LOG) ..."
READY=0
for i in $(seq 1 180); do
  if curl -sf "http://127.0.0.1:8000/health" >/dev/null 2>&1; then echo "vLLM ready after ~$((i*10))s"; READY=1; break; fi
  if ! kill -0 "$VLLM_PID" 2>/dev/null; then echo "!! vLLM exited early; tail:"; tail -60 "$VLLM_LOG"; exit 1; fi
  sleep 10
done
[ "$READY" = 1 ] || { echo "!! vLLM not ready in 30min; tail:"; tail -60 "$VLLM_LOG"; exit 1; }

export PYTHONPATH="$REPO"
"$REPO/.venv/bin/python" -u experiments/social_jira4/test_reasoning_vllm.py
STATUS=$?
echo "test exited status=$STATUS end=$(date)"
exit $STATUS
