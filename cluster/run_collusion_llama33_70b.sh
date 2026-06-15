#!/bin/bash
export HOME="${HOME:-/home/jtaraz}"
set -euo pipefail
export PYTHONUNBUFFERED=1
export PATH="$HOME/.local/bin:$PATH"

# ---- Job-specific: which model config to run ----
PROJECT=/fast/jtaraz/LIARS/colosseum
VENV="$PROJECT/.venv"
CONFIG="experiments/collusion/configs/collusion_jira_complete_n6_c2_regret_llama33_70b_local.yaml"
MODEL_LABEL="llama-3.3-70b-instruct"

# ---- HuggingFace cache (persistent fast disk; matches the YAML configs) ----
export HF_HOME=/fast/jtaraz/hf_cache
mkdir -p "$HF_HOME"
# Llama-3.3 is GATED: accept the license on HF and export HF_TOKEN before submitting.
if [ -z "${HF_TOKEN:-}" ]; then
    echo "WARNING: HF_TOKEN is not set — gated model meta-llama/Llama-3.3-70B-Instruct will fail to download." >&2
fi

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

# ---- vLLM runtime env (match your other jobs) ----
export VLLM_USE_FLASHINFER_SAMPLER=0
export VLLM_USE_DEEP_GEMM=0

# ---- Run the full sweep for this model ----
echo "running collusion sweep for $MODEL_LABEL with config $CONFIG"
python -m experiments.collusion.run --config "$CONFIG"
echo "done: $MODEL_LABEL"
