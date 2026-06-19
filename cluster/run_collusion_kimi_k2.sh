#!/bin/bash
export HOME="${HOME:-/home/jtaraz}"
set -euo pipefail
export PYTHONUNBUFFERED=1
export PATH="$HOME/.local/bin:$PATH"

# ---- Bypass the cluster HTTP proxy for localhost ----
# Compute nodes auto-set http_proxy/https_proxy. Without this, the vLLM readiness check
# (GET http://127.0.0.1:<port>/v1/models) gets routed through the proxy, never reaches the
# local server, and the run dies with TimeoutError after the full startup_timeout EVEN THOUGH
# vLLM logged "Application startup complete" (telltale: 0 incoming GET /v1/models in the vLLM
# log). This is exactly what killed both Kimi jobs on 2026-06-16.
export no_proxy="127.0.0.1,localhost,0.0.0.0,::1"
export NO_PROXY="127.0.0.1,localhost,0.0.0.0,::1"

# ---- Job-specific: which model config to run ----
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
VENV="$PROJECT/.venv"
CONFIG="experiments/collusion/configs/collusion_jira_complete_n6_c2_regret_kimi_k2_local.yaml"
MODEL_LABEL="kimi-k2-instruct"

# ---- HuggingFace cache (persistent fast disk; matches the YAML configs) ----
export HF_HOME=/fast/jtaraz/hf_cache
mkdir -p "$HF_HOME"
# Kimi-K2-Instruct is PUBLIC (not gated), so HF_TOKEN is optional here.
# NOTE: the checkpoint is ~1 TB (native FP8) -- make sure HF_HOME has the space and
# that the job's first-run download window is generous (startup_timeout in the YAML).
#
# NODE REQUIREMENT (HTCondor .submit, not set here): native FP8 ~1 TB only fits on an
# 8x B200 node (8 x ~183 GB = ~1.46 TB) on this cluster -- hosts i301-i407. In the
# .submit file request the whole node and pin the GPU type:
#     request_gpus = 8
#     requirements = (CUDADeviceName == "NVIDIA B200")
# 8x H100-80GB / 8x A100-80GB nodes are only 640 GB and will OOM on the FP8 weights;
# use cluster/run_collusion_kimi_k2_awq.sh (4-bit) for those instead.

cd "$PROJECT"

# ---- Sanity checks: fail loudly with a clear reason ----
if [ ! -f "$PROJECT/pyproject.toml" ]; then
    echo "ERROR: no pyproject.toml in $PROJECT — is this the colosseum repo?" >&2
    exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: 'uv' not found on PATH for this (compute) node. PATH=$PATH" >&2
    exit 1
fi
echo "using uv: $(command -v uv) ($(uv --version))"

# ---- Setup: (re)build the venv if missing or incomplete ----
if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c "import vllm" 2>/dev/null; then
    echo "venv missing or incomplete — (re)building with uv"
    rm -rf "$VENV"
    export UV_CACHE_DIR="${_CONDOR_SCRATCH_DIR:?scratch dir not set}/uv-cache"
    export UV_LINK_MODE=copy
    # Pull CoLLAB submodule if present (only needed by some envs; harmless for JiraTicket).
    git submodule update --init --recursive || echo "no submodules / skipping"
    uv venv --python 3.11 "$VENV"
    VIRTUAL_ENV="$VENV" uv sync --no-install-project --extra vllm
fi

# ---- Verify the venv is actually usable BEFORE sourcing it ----
if [ ! -f "$VENV/bin/activate" ]; then
    echo "ERROR: build did not produce $VENV/bin/activate — see the uv output above." >&2
    exit 1
fi

echo "activating venv: $VENV"
source "$VENV/bin/activate"
python -c "import vllm; print('OK: vllm', vllm.__version__)"
# Kimi-K2's tool-call parser ("kimi_k2") requires a recent vLLM (>= ~0.10).
# If the server later rejects --tool-call-parser kimi_k2, the vLLM build is too old.

# ---- CUDA toolkit (nvcc) for runtime kernel JIT compilation ----
# Compute nodes ship only the CUDA runtime/driver, NOT the dev toolkit, so quant-MoE paths
# that JIT-compile kernels (FP8 on Blackwell here; MXFP4 Triton on Hopper) die with
# "Could not find nvcc and default cuda_home='/usr/local/cuda' doesn't exist". Load a modern
# CUDA module so CUDA_HOME/PATH point at a real nvcc (12.9 covers Blackwell sm_100 + Hopper).
# These env vars propagate into the vLLM subprocess via os.environ.copy().
source /etc/profile.d/modules.sh 2>/dev/null || true
module load cuda/12.9 2>/dev/null || echo "WARNING: 'module load cuda/12.9' failed — FP8 kernel JIT may fail"
echo "CUDA_HOME=${CUDA_HOME:-<unset>}; nvcc=$(command -v nvcc || echo MISSING)"

# ---- vLLM runtime env (match your other jobs) ----
export VLLM_USE_FLASHINFER_SAMPLER=0
export VLLM_USE_DEEP_GEMM=0

# ---- Run the full sweep for this model ----
# NOTE: Kimi-K2 native FP8 (~1 TB) is sized for an 8x B200 node (tensor_parallel_size: 8
# in the YAML). It does NOT fit on 8x80GB nodes -- use the _awq (4-bit) config for those.
echo "running collusion sweep for $MODEL_LABEL with config $CONFIG"
python -m experiments.collusion.run --config "$CONFIG"
echo "done: $MODEL_LABEL"
