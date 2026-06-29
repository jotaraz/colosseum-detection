#!/bin/bash
export HOME="${HOME:-/home/jtaraz}"
set -euo pipefail
export PYTHONUNBUFFERED=1
export PATH="$HOME/.local/bin:$PATH"

# Bypass the cluster HTTP proxy for localhost (else the vLLM readiness check times out).
export no_proxy="127.0.0.1,localhost,0.0.0.0,::1"
export NO_PROXY="127.0.0.1,localhost,0.0.0.0,::1"

PROJECT=/fast/jtaraz/LIARS/colosseum-detection
# SMOKE TEST: builds/validates the upgraded vLLM venv (>=0.23.0) shared by GLM + Qwen3.6.
VENV="$PROJECT/.venv-vllm023"
VLLM_TARGET="0.23.0"
CONFIG="experiments/social_jira2/configs/social_jira2_smoke_glm_4_7_flash_local.yaml"
MODEL_LABEL="SMOKE-glm-4.7-flash-vllm023"

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

# Build the upgraded venv: sync terrarium[vllm] (pulls 0.12.0) then force vLLM up to $VLLM_TARGET.
# This step is the core thing the smoke test validates (terrarium 0.1.1 may pin vllm==0.12.0).
if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c "import vllm,sys; sys.exit(0 if vllm.__version__.split('.')[:2] >= '$VLLM_TARGET'.split('.')[:2] else 1)" 2>/dev/null; then
    echo "upgraded venv missing or wrong vLLM — (re)building with uv (target vllm==$VLLM_TARGET)"
    rm -rf "$VENV"
    export UV_CACHE_DIR="${_CONDOR_SCRATCH_DIR:?scratch dir not set}/uv-cache"
    export UV_LINK_MODE=copy
    git submodule update --init --recursive || echo "no submodules / skipping"
    uv venv --python 3.11 "$VENV"
    VIRTUAL_ENV="$VENV" uv sync --no-install-project --extra vllm
    VIRTUAL_ENV="$VENV" uv pip install --upgrade "vllm==$VLLM_TARGET"
fi

if [ ! -f "$VENV/bin/activate" ]; then
    echo "ERROR: build did not produce $VENV/bin/activate — see the uv output above." >&2
    exit 1
fi

echo "activating venv: $VENV"
source "$VENV/bin/activate"
python -c "import vllm; print('OK: vllm', vllm.__version__)"
# Confirm terrarium still imports against the upgraded vLLM (the key compat risk).
python -c "import llm_server, terrarium_agents; print('OK: terrarium imports against vllm', __import__('vllm').__version__)" \
  || echo "WARN: terrarium import check failed — inspect before the full sweep"

python "$PROJECT/cluster/patch_vllm_client.py" "$VENV/lib/python3.11/site-packages/llm_server/clients/vllm_client.py" || echo "WARN: vllm_client patch failed"

source /etc/profile.d/modules.sh 2>/dev/null || true
module load cuda/12.9 2>/dev/null || echo "WARNING: 'module load cuda/12.9' failed — kernel JIT may fail"
echo "CUDA_HOME=${CUDA_HOME:-<unset>}; nvcc=$(command -v nvcc || echo MISSING)"

export VLLM_USE_FLASHINFER_SAMPLER=0
export VLLM_USE_DEEP_GEMM=0

echo "running SOCIAL-JIRA2 SMOKE for $MODEL_LABEL with config $CONFIG"
python -m experiments.social_jira2.run --config "$CONFIG"
echo "done: $MODEL_LABEL"
