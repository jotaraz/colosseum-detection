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
CONFIG="experiments/collusion/configs/collusion_jira_complete_n6_c2_regret_gptoss_120b_local.yaml"
MODEL_LABEL="gpt-oss-120b"

# ---- HuggingFace cache (persistent fast disk; matches the YAML configs) ----
export HF_HOME=/fast/jtaraz/hf_cache
mkdir -p "$HF_HOME"
# openai/gpt-oss-120b is PUBLIC (Apache-2.0), so HF_TOKEN is optional here.
# The checkpoint is ~63 GB native MXFP4 -- first run downloads it into HF_HOME (a few
# minutes on the shared link), cached runs skip straight to load.
#
# NODE REQUIREMENT (HTCondor .submit): MXFP4 ~63 GB fits on 2x H100-80GB at TP=2. In the
# .submit file request the two GPUs and pin the card type:
#     request_gpus = 2
#     requirements = (CUDADeviceName == "NVIDIA H100 80GB HBM3")

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
# gpt-oss tool/reasoning parsers ("openai" / "openai_gptoss") need a recent vLLM (>= 0.10).
# If the server rejects --tool-call-parser openai / --reasoning-parser openai_gptoss, the
# vLLM build is too old for gpt-oss.

# ---- CUDA toolkit (nvcc) for runtime kernel JIT compilation ----
# Compute nodes ship only the CUDA runtime/driver, NOT the dev toolkit, so the MXFP4 MoE
# Triton kernels gpt-oss compiles at load time hang/fail without nvcc ("Could not find nvcc
# and default cuda_home='/usr/local/cuda' doesn't exist"). Load a modern CUDA module so
# CUDA_HOME/PATH point at a real nvcc. These propagate into the vLLM subprocess.
source /etc/profile.d/modules.sh 2>/dev/null || true
module load cuda/12.9 2>/dev/null || echo "WARNING: 'module load cuda/12.9' failed — MXFP4 kernel JIT may fail"
echo "CUDA_HOME=${CUDA_HOME:-<unset>}; nvcc=$(command -v nvcc || echo MISSING)"

# ---- vLLM runtime env (match your other jobs) ----
export VLLM_USE_FLASHINFER_SAMPLER=0
export VLLM_USE_DEEP_GEMM=0

# ---- Run the full sweep for this model ----
# NOTE: gpt-oss-120b MXFP4 (~63 GB) runs on 2x H100-80GB (tensor_parallel_size: 2 in the YAML).
echo "running collusion sweep for $MODEL_LABEL with config $CONFIG"
python -m experiments.collusion.run --config "$CONFIG"
echo "done: $MODEL_LABEL"
