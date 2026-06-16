#!/bin/bash
export HOME="${HOME:-/home/jtaraz}"
set -euo pipefail
export PYTHONUNBUFFERED=1
export PATH="$HOME/.local/bin:$PATH"

# ---- Job-specific: which model config to run ----
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
VENV="$PROJECT/.venv"
CONFIG="experiments/collusion/configs/collusion_jira_complete_n6_c2_regret_kimi_k2_awq_local.yaml"
MODEL_LABEL="kimi-k2-instruct-awq"

# ---- HuggingFace cache (persistent fast disk; matches the YAML configs) ----
export HF_HOME=/fast/jtaraz/hf_cache
mkdir -p "$HF_HOME"
# Public quant repo -> HF_TOKEN optional. ~560-630 GB download; ensure HF_HOME has room.
#
# NODE REQUIREMENT (HTCondor .submit, not set here): the 4-bit AWQ weights fit on a single
# 8x 80GB node (640 GB). On this cluster that's the plentiful A100-SXM4-80GB nodes (or 8x
# H100-80GB). In the .submit file request the whole node, e.g.:
#     request_gpus = 8
#     requirements = (CUDAGlobalMemoryMb >= 80000)
# (A100 has no native FP8, but AWQ INT4 runs there via Marlin -- that's why this is the
# A100-friendly variant; the FP8 release needs an 8x B200 node, see run_collusion_kimi_k2.sh.)

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
# Two things to watch on first run:
#  - QuixiAI/Kimi-K2-Instruct-AWQ's model card reports a vLLM weight-loading bug; if the
#    server dies with a tensor-dimension mismatch during load, try another quant repo / newer vLLM.
#  - Kimi-K2's tool-call parser ("kimi_k2") needs vLLM >= ~0.10.

# ---- vLLM runtime env (match your other jobs) ----
export VLLM_USE_FLASHINFER_SAMPLER=0
export VLLM_USE_DEEP_GEMM=0

# ---- Run the full sweep for this model ----
# NOTE: 4-bit Kimi-K2 on 8x80GB is memory-tight (weights ~560-630 GB in 640 GB). The YAML
# uses gpu_memory_utilization 0.95 + small max_model_len + --enforce-eager. If it OOMs at
# load, lower max_model_len further before anything else.
echo "running collusion sweep for $MODEL_LABEL with config $CONFIG"
python -m experiments.collusion.run --config "$CONFIG"
echo "done: $MODEL_LABEL"
