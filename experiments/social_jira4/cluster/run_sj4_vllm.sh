#!/bin/bash
# social_jira4 TARGET via LOCAL vLLM — mirrors social_jira3's proven qwen3.6 run script
# (cluster/run_social_jira3_c2p2_qwen3_6_35b_a3b_*.sh). The framework's `provider: vllm`
# (auto_start_server, in the config) launches + health-checks vLLM itself; this wrapper only
# prepares the environment and runs the sj4 loop. Judges/prompter/validator/referee stay on
# OpenRouter (the client loads .env + uses the proxy; localhost is bypassed via no_proxy).
#
# Args: $1 = target config (repo-relative)   $2 = model-label   $3 = out-dir (repo-relative)
export HOME="${HOME:-/home/jtaraz}"
set -euo pipefail
export PYTHONUNBUFFERED=1
export PATH="$HOME/.local/bin:$PATH"
case ":$PATH:" in *":/usr/bin:"*) ;; *) export PATH="$PATH:/usr/bin:/bin";; esac  # ensure `ld` is findable

# Bypass the cluster HTTP proxy for localhost (else the vLLM readiness check times out).
export no_proxy="127.0.0.1,localhost,0.0.0.0,::1"
export NO_PROXY="127.0.0.1,localhost,0.0.0.0,::1"

CONFIG="${1:?usage: run_sj4_vllm.sh <config> <label> <out-dir>}"
LABEL="${2:?model label}"
OUT="${3:?out dir}"

PROJECT=/fast/jtaraz/LIARS/colosseum-detection
VENV="$PROJECT/.venv-vllm023"        # vLLM 0.23 — Qwen3.6 gated-delta-net arch needs >=0.23 (NOT .venv's 0.12)
export HF_HOME=/fast/jtaraz/hf_cache; mkdir -p "$HF_HOME"
export PYTHONPATH="$PROJECT"
cd "$PROJECT"

# Require the PREBUILT shared venv (do NOT rebuild inline — GLM/Qwen jobs share it; a concurrent
# rebuild on /fast would corrupt it).  Build once via: condor_submit_bid 50 cluster/build_venv_vllm023.sub
if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c "import vllm, llm_server" 2>/dev/null; then
    echo "ERROR: $VENV missing/invalid (need vllm>=0.23 AND llm_server)." >&2
    echo "       Build it first: condor_submit_bid 50 cluster/build_venv_vllm023.sub" >&2
    exit 1
fi
echo "activating venv: $VENV"
source "$VENV/bin/activate"
python -c "import vllm; print('OK: vllm', vllm.__version__)"

# Forward reasoning_effort / sampling through the framework vLLM client (same patch jira3 applies).
python "$PROJECT/cluster/patch_vllm_client.py" "$VENV/lib/python3.11/site-packages/llm_server/clients/vllm_client.py" || echo "WARN: vllm_client patch failed"

# CUDA toolkit (nvcc + ld) for the GDN linear-attn Triton kernels Qwen3.6 JIT-compiles at load.
source /etc/profile.d/modules.sh 2>/dev/null || true
module load cuda/12.9 2>/dev/null || echo "WARNING: 'module load cuda/12.9' failed"
if ! command -v nvcc >/dev/null 2>&1; then    # fallback: point directly at cuda-13.2 (matches torch cu130)
    export CUDA_HOME=/is/software/nvidia/cuda-13.2
    export PATH="$CUDA_HOME/bin:$PATH"
    export LD_LIBRARY_PATH="$CUDA_HOME/lib64:${LD_LIBRARY_PATH:-}"
fi
echo "CUDA_HOME=${CUDA_HOME:-<unset>}; nvcc=$(command -v nvcc || echo MISSING); ld=$(command -v ld || echo MISSING)"

export VLLM_USE_FLASHINFER_SAMPLER=0
export VLLM_USE_DEEP_GEMM=0

echo "node=$(hostname) start=$(date) config=$CONFIG label=$LABEL out=$OUT"
python -u -m experiments.social_jira4.loop --mode live --steps 8 --seeds 1,2,3 \
    --config "$CONFIG" --model-label "$LABEL" --out-dir "$OUT"
echo "done: $LABEL end=$(date)"
