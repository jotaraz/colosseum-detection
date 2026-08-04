#!/bin/bash
# social_jira4 — ONE meta-gate optimization loop against ONE local vLLM target on 2 GPUs.
#
#   usage: run_sj4_metagate_1model.sh <out-dir> <port> <question> <steps> <prompter> <model> [panel] [extra...]
#
#     out-dir     repo-relative; the generated config and cost.json land here
#     port        ONE free port for this job's single server (not a base — there is only one server)
#     question    fabrication | admissibility | realism
#     steps       optimization steps
#     prompter    dspro (deepseek-v4-pro / OpenRouter) | gpt54 (Azure)
#     model       qwen | gptoss  — which target this job serves
#     panel       meta-gate judges, comma-separated (default "dspro,gpt54")
#
# WHY THIS EXISTS, and what it costs. run_sj4_metagate.sh asks for 4 GPUs and runs BOTH targets in
# one job, scoring every candidate over 6 seeds spanning two models. Those jobs stopped placing:
# the pool had 24 H100s free but fragmented across nodes holding 3, 2 and 1, so no 4-GPU hole
# existed anywhere. This script halves the job — 2 GPUs, one model, three seeds — so two of them
# fit where one dual job could not.
#
# The cost is not just bookkeeping. A dual run's score mixes model variance with seed variance and
# the prompter optimizes against both targets at once. Split, each loop optimizes against ONE
# model's three seeds, so scores from these runs are NOT comparable to v4f/v4g/v4h/v4i. Meta-gate
# pass rates ARE comparable — the gate reads the prompt and never sees a rollout.
#
# Everything else — venv, CUDA module, credentials, prompter wiring, the serial server pre-start,
# the per-job CWD for vLLM logs — is identical to run_sj4_metagate.sh, and the reasoning behind
# each is documented there.
export HOME="${HOME:-/home/jtaraz}"
set -euo pipefail
export PYTHONUNBUFFERED=1
export PATH="$HOME/.local/bin:$PATH"
case ":$PATH:" in *":/usr/bin:"*) ;; *) export PATH="$PATH:/usr/bin:/bin";; esac

export no_proxy="127.0.0.1,localhost,0.0.0.0,::1"
export NO_PROXY="127.0.0.1,localhost,0.0.0.0,::1"

OUT="${1:?usage: run_sj4_metagate_1model.sh <out-dir> <port> <question> <steps> <prompter> <model> [panel] [extra...]}"
PORT="${2:?port}"
QUESTION="${3:?meta-gate question}"
STEPS="${4:?steps}"
PROMPTER="${5:?prompter: dspro | gpt54}"
MODEL="${6:?model: qwen | gptoss}"
shift 6
PANEL="dspro,gpt54"
if [ "$#" -gt 0 ] && [ "${1#-}" = "$1" ]; then
    PANEL="$1"
    shift
fi

PROJECT=/fast/jtaraz/LIARS/colosseum-detection
VENV="$PROJECT/.venv-vllm023"
export HF_HOME=/fast/jtaraz/hf_cache; mkdir -p "$HF_HOME"
export PYTHONPATH="$PROJECT"
export TMPDIR=/fast/jtaraz/tmp; mkdir -p "$TMPDIR"
cd "$PROJECT"

if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c "import vllm, llm_server" 2>/dev/null; then
    echo "ERROR: $VENV missing/invalid (need vllm>=0.23 AND llm_server)." >&2
    exit 1
fi
source "$VENV/bin/activate"
python -c "import vllm; print('OK: vllm', vllm.__version__)"

python "$PROJECT/cluster/patch_vllm_client.py" "$VENV/lib/python3.11/site-packages/llm_server/clients/vllm_client.py" || echo "WARN: vllm_client patch failed"

source /etc/profile.d/modules.sh 2>/dev/null || true
module load cuda/12.9 2>/dev/null || echo "WARNING: 'module load cuda/12.9' failed"
if ! command -v nvcc >/dev/null 2>&1; then
    export CUDA_HOME=/is/software/nvidia/cuda-13.2
    export PATH="$CUDA_HOME/bin:$PATH"
    export LD_LIBRARY_PATH="$CUDA_HOME/lib64:${LD_LIBRARY_PATH:-}"
fi
export VLLM_USE_FLASHINFER_SAMPLER=0
export VLLM_USE_DEEP_GEMM=0

# ---- fail fast on a sick GPU (see run_sj4_metagate.sh for the v4b history) --------------------
if ! nvidia-smi -L >/dev/null 2>&1; then
    echo "FATAL: nvidia-smi cannot enumerate GPUs on $(hostname). Bad node — aborting." >&2
    nvidia-smi -L >&2 || true
    exit 42
fi
python - <<'PY' >&2 || { echo "FATAL: CUDA unusable on $(hostname). Bad node — aborting." >&2; exit 42; }
import sys, torch
n = torch.cuda.device_count()
if n < 2:
    print(f"GPU preflight FAILED: {n} CUDA devices visible, need 2", file=sys.stderr)
    sys.exit(1)
for i in range(n):
    torch.zeros(8, device=f"cuda:{i}")
print(f"GPU preflight OK: {n} devices usable")
PY

# ---- this job's GPUs all go to the one server -------------------------------------------------
# HTCondor hands us whichever 2 devices it allocated; they are not necessarily 0,1. Both go to the
# single server at tensor_parallel_size 2, which is what each model needs (qwen bf16 ~70 GB,
# gpt-oss mxfp4 ~63 GB — neither fits one 80 GB card alongside its KV cache at these context
# lengths, which is why the split is by model rather than by seed within a model).
ALLOC="${CUDA_VISIBLE_DEVICES:-0,1}"
IFS=',' read -r -a GPUS <<< "$ALLOC"
if [ "${#GPUS[@]}" -lt 2 ]; then
    echo "ERROR: need 2 GPUs, got ${#GPUS[@]} ($ALLOC). request_gpus must be 2." >&2
    exit 1
fi
SERVER_GPUS="${GPUS[0]},${GPUS[1]}"

case "$MODEL" in
    qwen)
        TMPL="$PROJECT/experiments/social_jira4/configs/social_jira4_qwen_only_r4.yaml.tmpl"
        PORT_KEY="__QWEN_PORT__"; GPUS_KEY="__QWEN_GPUS__"
        DEF_SEEDS="1,2,3"
        DEF_LABELS="vllm-qwen3.6-35b-a3b,vllm-qwen3.6-35b-a3b,vllm-qwen3.6-35b-a3b"
        ;;
    gptoss)
        TMPL="$PROJECT/experiments/social_jira4/configs/social_jira4_gptoss_only_r4.yaml.tmpl"
        PORT_KEY="__GPTOSS_PORT__"; GPUS_KEY="__GPTOSS_GPUS__"
        DEF_SEEDS="4,5,6"
        DEF_LABELS="vllm-gpt-oss-120b,vllm-gpt-oss-120b,vllm-gpt-oss-120b"
        ;;
    *)
        echo "ERROR: unknown model '$MODEL' (expected qwen | gptoss)" >&2
        exit 1
        ;;
esac

mkdir -p "$PROJECT/$OUT"
CONFIG="$OUT/target_config.yaml"
sed -e "s/$PORT_KEY/$PORT/" -e "s/$GPUS_KEY/$SERVER_GPUS/" "$TMPL" > "$PROJECT/$CONFIG"
grep -q "__" "$PROJECT/$CONFIG" && { echo "ERROR: unsubstituted placeholder in $CONFIG" >&2; exit 1; }

JOBCWD="$PROJECT/$OUT/jobcwd"
mkdir -p "$JOBCWD"
ABS_CONFIG="$PROJECT/$CONFIG"
ABS_OUT="$PROJECT/$OUT"
cd "$JOBCWD"

set -a; source "$PROJECT/.env" 2>/dev/null || true; set +a
AZURE_ENV=/fast/jtaraz/syco-bench/.env
if [ -f "$AZURE_ENV" ]; then
    set -a; source "$AZURE_ENV"; set +a
else
    echo "WARNING: $AZURE_ENV not found — the gpt-5.4 meta-judge will fail, and with it EVERY gate" >&2
fi

case "$PROMPTER" in
    dspro)
        PROMPTER_ARGS=(--prompter-provider openrouter --prompter-model deepseek/deepseek-v4-pro)
        ;;
    gpt54)
        PROMPTER_ARGS=(--prompter-provider azure
                       --prompter-model "${AZURE_JUDGE_DEPLOYMENT:-gpt-5.4}"
                       --prompter-max-tokens 16000)
        ;;
    *)
        echo "ERROR: unknown prompter '$PROMPTER' (expected dspro | gpt54)" >&2
        exit 1
        ;;
esac

# ---- start the target server ONCE, before the loop (see run_sj4_metagate.sh, the v4c race) -----
echo "starting target server (before the loop) ..."
python "$PROJECT/experiments/social_jira4/cluster/diag_vllm.py" "$ABS_CONFIG" || {
    echo "FATAL: target server did not come up — see $JOBCWD/logs/vllm/. Aborting." >&2
    exit 43
}

echo "node=$(hostname) start=$(date) git=$(git -C "$PROJECT" rev-parse --short HEAD)"
echo "cwd=$JOBCWD (vllm server logs land here)"
echo "out=$OUT question=$QUESTION steps=$STEPS prompter=$PROMPTER model=$MODEL panel=$PANEL extra=$*"
echo "gpus: alloc=$ALLOC server=$SERVER_GPUS:$PORT"
echo "azure_endpoint=${AZURE_OPENAI_ENDPOINT:-unset} azure_deployment=${AZURE_JUDGE_DEPLOYMENT:-gpt-5.4}"
echo "azure_key=$([ -n "${AZURE_OPENAI_API_KEY:-}" ] && echo set || echo MISSING)"
echo "openrouter_key=$([ -n "${OPENROUTER_API_KEY:-}" ] && echo set || echo MISSING)"

exec python -u -m experiments.social_jira4.loop --mode live \
    --steps "$STEPS" \
    --seeds "$DEF_SEEDS" \
    --config "$ABS_CONFIG" \
    --model-label "$DEF_LABELS" \
    "${PROMPTER_ARGS[@]}" \
    --meta-gate "$PANEL" \
    --meta-gate-question "$QUESTION" \
    --meta-gate-view system_user \
    --repair-attempts 5 \
    --out-dir "$ABS_OUT" \
    "$@"
