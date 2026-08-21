#!/bin/bash
# social_jira3 DECEPTION sweep — one local vLLM target, one arm (base | harmless).
#
#   usage: run_social_jira3_deception_vllm.sh <repo-relative-config.yaml>
#
# See experiments/social_jira3/social_jira3_allow_forbid_plan.md for the study. This script is
# the sj3 vLLM launcher pattern (cluster/run_social_jira3_c2p2_*_v6_confsweep.sh) unchanged in
# every respect that matters; only the config is a parameter instead of hard-coded, because the
# deception sweep needs four of them (base|harmless x gptoss|qwen).
#
# ONE VENV FOR BOTH MODELS. `.venv-vllm023` (vllm >= 0.23) serves gpt-oss-120b as well as
# Qwen3.6-35B-A3B — sj4's rr10dcp runs gpt-oss on exactly this venv. The older note that gpt-oss
# needs the pinned 0.12.0 venv is obsolete: what it actually needs is `module load cuda/12.9`
# below, without which the MXFP4/Triton path hangs forever at load ("Using Triton backend").
#
# `robust_assignment: true` IS SET IN THE CONFIGS and is NOT the historical sj3 setting for
# gpt-oss (its v6 confsweep tree ran false). It is set here so the prompt is identical across all
# six models of the study. gpt-oss has never run under it — watch the first few RUN END lines.
#
# Submit with `condor_submit_bid 100 cluster/run_social_jira3_deception_<arm>_<model>.sub`.
export HOME="${HOME:-/home/jtaraz}"
set -euo pipefail
export PYTHONUNBUFFERED=1
export PATH="$HOME/.local/bin:$PATH"
case ":$PATH:" in *":/usr/bin:"*) ;; *) export PATH="$PATH:/usr/bin:/bin";; esac

# The vLLM readiness check talks to 127.0.0.1 and must not be sent through the proxy.
export no_proxy="127.0.0.1,localhost,0.0.0.0,::1"
export NO_PROXY="127.0.0.1,localhost,0.0.0.0,::1"

CONFIG="${1:?usage: run_social_jira3_deception_vllm.sh <repo-relative config.yaml>}"

PROJECT=/fast/jtaraz/LIARS/colosseum-detection
VENV="$PROJECT/.venv-vllm023"
VLLM_TARGET="0.23.0"

export HF_HOME=/fast/jtaraz/hf_cache; mkdir -p "$HF_HOME"
export TMPDIR=/fast/jtaraz/tmp; mkdir -p "$TMPDIR"
export PYTHONPATH="$PROJECT"
cd "$PROJECT"

[ -f "$PROJECT/pyproject.toml" ] || { echo "ERROR: no pyproject.toml in $PROJECT" >&2; exit 1; }
[ -f "$PROJECT/$CONFIG" ] || { echo "ERROR: config not found: $PROJECT/$CONFIG" >&2; exit 1; }

# Require the PREBUILT shared venv. Do NOT rebuild inline: several jobs share it and a concurrent
# rebuild on /fast (no file locking) would corrupt it. Build once via build_venv_vllm023.sub.
if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c "import vllm,llm_server,sys; sys.exit(0 if vllm.__version__.split('.')[:2] >= '$VLLM_TARGET'.split('.')[:2] else 1)" 2>/dev/null; then
    echo "ERROR: $VENV missing/invalid (need vllm>=$VLLM_TARGET AND llm_server)." >&2
    echo "       Build it first: condor_submit_bid 50 cluster/build_venv_vllm023.sub" >&2
    exit 1
fi
source "$VENV/bin/activate"
python -c "import vllm; print('OK: vllm', vllm.__version__)"

# Forward reasoning_effort / sampling params through the vLLM client.
python "$PROJECT/cluster/patch_vllm_client.py" "$VENV/lib/python3.11/site-packages/llm_server/clients/vllm_client.py" || echo "WARN: vllm_client patch failed"

# CUDA toolkit (nvcc): required by the MXFP4 (gpt-oss) and MoE JIT paths. The bare `module load
# cuda` gives the ancient 6.0 and is useless — the explicit version matters.
source /etc/profile.d/modules.sh 2>/dev/null || true
module load cuda/12.9 2>/dev/null || echo "WARNING: 'module load cuda/12.9' failed — kernel JIT may fail"
echo "CUDA_HOME=${CUDA_HOME:-<unset>}; nvcc=$(command -v nvcc || echo MISSING)"

export VLLM_USE_FLASHINFER_SAMPLER=0
export VLLM_USE_DEEP_GEMM=0

echo "node=$(hostname) start=$(date)"
echo "config=$CONFIG"
grep -E "^  tag:|^    deception:|^  robust_assignment:|^  seeds:" "$PROJECT/$CONFIG" || true

python -m experiments.social_jira3.run --config "$CONFIG"
echo "done: $CONFIG"
