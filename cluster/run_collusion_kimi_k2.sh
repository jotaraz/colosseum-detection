#!/bin/bash
export HOME="${HOME:-/home/jtaraz}"
set -euo pipefail
export PYTHONUNBUFFERED=1
export PATH="$HOME/.local/bin:$PATH"

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

# ---- vLLM runtime env (match your other jobs) ----
export VLLM_USE_FLASHINFER_SAMPLER=0
export VLLM_USE_DEEP_GEMM=0

# ---- Run the full sweep for this model ----
# NOTE: Kimi-K2 native FP8 (~1 TB) is sized for an 8x B200 node (tensor_parallel_size: 8
# in the YAML). It does NOT fit on 8x80GB nodes -- use the _awq (4-bit) config for those.
echo "running collusion sweep for $MODEL_LABEL with config $CONFIG"
python -m experiments.collusion.run --config "$CONFIG"
echo "done: $MODEL_LABEL"
