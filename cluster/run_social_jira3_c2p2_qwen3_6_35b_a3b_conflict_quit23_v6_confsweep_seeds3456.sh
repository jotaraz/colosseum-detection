#!/bin/bash
export HOME="${HOME:-/home/jtaraz}"
set -euo pipefail
export PYTHONUNBUFFERED=1
export PATH="$HOME/.local/bin:$PATH"

# Bypass the cluster HTTP proxy for localhost (else the vLLM readiness check times out).
export no_proxy="127.0.0.1,localhost,0.0.0.0,::1"
export NO_PROXY="127.0.0.1,localhost,0.0.0.0,::1"

PROJECT=/fast/jtaraz/LIARS/colosseum-detection
# Separate venv: GLM-4.7-Flash / Qwen3.6 arch needs vLLM >= ~0.23.0, NOT the pinned 0.12.0.
VENV="$PROJECT/.venv-vllm023"
VLLM_TARGET="0.23.0"
CONFIG="experiments/social_jira3/configs/social_jira3_c2p2_qwen3_6_35b_a3b_conflict_quit23_v6_confsweep_seeds3456.yaml"
MODEL_LABEL="qwen3.6-35b-a3b-social-jira3-conflict-confsweep-seeds3456"

export HF_HOME=/fast/jtaraz/hf_cache
mkdir -p "$HF_HOME"

cd "$PROJECT"

if [ ! -f "$PROJECT/pyproject.toml" ]; then
    echo "ERROR: no pyproject.toml in $PROJECT — is this the colosseum repo?" >&2
    exit 1
fi

# Require the PREBUILT shared venv. Do NOT rebuild inline: GLM and Qwen jobs share this venv,
# and a concurrent rebuild on /fast would corrupt it. Build once via:
#   condor_submit_bid 50 cluster/build_venv_vllm023.sub
if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c "import vllm,llm_server,sys; sys.exit(0 if vllm.__version__.split('.')[:2] >= '$VLLM_TARGET'.split('.')[:2] else 1)" 2>/dev/null; then
    echo "ERROR: $VENV missing/invalid (need vllm>=$VLLM_TARGET AND terrarium)." >&2
    echo "       Build it first: condor_submit_bid 50 cluster/build_venv_vllm023.sub" >&2
    exit 1
fi

echo "activating venv: $VENV"
source "$VENV/bin/activate"
python -c "import vllm; print('OK: vllm', vllm.__version__)"

# Forward reasoning_effort / sampling through the vLLM client.
python "$PROJECT/cluster/patch_vllm_client.py" "$VENV/lib/python3.11/site-packages/llm_server/clients/vllm_client.py" || echo "WARN: vllm_client patch failed"

# CUDA toolkit (nvcc) in case the MoE path JIT-compiles kernels at load time.
source /etc/profile.d/modules.sh 2>/dev/null || true
module load cuda/12.9 2>/dev/null || echo "WARNING: 'module load cuda/12.9' failed — kernel JIT may fail"
echo "CUDA_HOME=${CUDA_HOME:-<unset>}; nvcc=$(command -v nvcc || echo MISSING)"

export VLLM_USE_FLASHINFER_SAMPLER=0
export VLLM_USE_DEEP_GEMM=0

echo "running SOCIAL-JIRA3 for $MODEL_LABEL with config $CONFIG"
python -m experiments.social_jira3.run --config "$CONFIG"
echo "done: $MODEL_LABEL"
