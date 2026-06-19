#!/bin/bash
export HOME="${HOME:-/home/jtaraz}"
set -euo pipefail
export PYTHONUNBUFFERED=1
export PATH="$HOME/.local/bin:$PATH"

# Bypass the cluster HTTP proxy for localhost (else the vLLM readiness check times out).
export no_proxy="127.0.0.1,localhost,0.0.0.0,::1"
export NO_PROXY="127.0.0.1,localhost,0.0.0.0,::1"

PROJECT=/fast/jtaraz/LIARS/colosseum-detection
VENV="$PROJECT/.venv"
CONFIG="experiments/self_sacrifice/configs/self_sacrifice_obvious_n3_gptoss_120b_local.yaml"
MODEL_LABEL="gpt-oss-120b-self-sacrifice-obvious"

export HF_HOME=/fast/jtaraz/hf_cache
mkdir -p "$HF_HOME"

cd "$PROJECT"

if [ ! -f "$PROJECT/pyproject.toml" ]; then
    echo "ERROR: no pyproject.toml in $PROJECT — is this the colosseum repo?" >&2
    exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: 'uv' not found on PATH for this (compute) node. PATH=$PATH" >&2
    exit 1
fi
echo "using uv: $(command -v uv) ($(uv --version))"

if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c "import vllm" 2>/dev/null; then
    echo "venv missing or incomplete — (re)building with uv"
    rm -rf "$VENV"
    export UV_CACHE_DIR="${_CONDOR_SCRATCH_DIR:?scratch dir not set}/uv-cache"
    export UV_LINK_MODE=copy
    git submodule update --init --recursive || echo "no submodules / skipping"
    uv venv --python 3.11 "$VENV"
    VIRTUAL_ENV="$VENV" uv sync --no-install-project --extra vllm
fi

if [ ! -f "$VENV/bin/activate" ]; then
    echo "ERROR: build did not produce $VENV/bin/activate — see the uv output above." >&2
    exit 1
fi

echo "activating venv: $VENV"
source "$VENV/bin/activate"
python -c "import vllm; print('OK: vllm', vllm.__version__)"

# Forward reasoning_effort through the vLLM client so configs can lower gpt-oss reasoning.
python "$PROJECT/cluster/patch_vllm_client.py" "$VENV/lib/python3.11/site-packages/llm_server/clients/vllm_client.py" || echo "WARN: vllm_client patch failed"

# CUDA toolkit (nvcc) for the MXFP4 Triton MoE kernels gpt-oss compiles at load time.
source /etc/profile.d/modules.sh 2>/dev/null || true
module load cuda/12.9 2>/dev/null || echo "WARNING: 'module load cuda/12.9' failed — MXFP4 kernel JIT may fail"
echo "CUDA_HOME=${CUDA_HOME:-<unset>}; nvcc=$(command -v nvcc || echo MISSING)"

export VLLM_USE_FLASHINFER_SAMPLER=0
export VLLM_USE_DEEP_GEMM=0

echo "running SELF-SACRIFICE sweep for $MODEL_LABEL with config $CONFIG"
python -m experiments.self_sacrifice.run --config "$CONFIG"
echo "done: $MODEL_LABEL"
