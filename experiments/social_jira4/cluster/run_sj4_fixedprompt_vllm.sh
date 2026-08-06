#!/bin/bash
# social_jira4 — replay the rr10 prompt set against ONE local vLLM target, at ONE deception level.
#
#   usage: run_sj4_fixedprompt_vllm.sh <out-dir> <port> <model> <deception> [extra fixed_prompt_run flags...]
#
#     out-dir     repo-relative; the generated config and the rollouts land here
#     port        ONE free port for this job's single server (not a base — there is one server)
#     model       qwen | gptoss  — which target this job serves
#     deception   none | allow | forbid  — the run-level line appended to the system prompt
#
# WHAT THIS IS. run_sj4_fixedprompt.sh runs the same replay for OpenRouter targets and needs no
# GPUs. gpt-oss-120b and qwen3.6-35b-a3b are served locally, so this script adds exactly the vLLM
# machinery run_sj4_metagate_1model.sh already carries — venv-vllm023, the CUDA module, the sick-
# node preflight, template substitution, the serial pre-start of the server — and then execs
# fixed_prompt_run.py instead of the optimization loop. Everything vLLM-shaped here is lifted from
# that script; the reasoning behind each piece is documented at length in run_sj4_metagate.sh.
#
# THE CONFIGS ARE NOT NEW. This reuses configs/social_jira4_{qwen,gptoss}_only_r4.yaml.tmpl
# verbatim — the same templates the meta-gate split jobs run. Their simulation / environment /
# communication_network blocks were diffed against social_jira4_realrun_pinned_or.yaml (the config
# the four OpenRouter base cells ran under) and are IDENTICAL; only the cosmetic `experiment.tag`
# differs. So a vLLM cell and an OpenRouter cell of this study differ in the target model and
# nothing else. Writing rr10-specific copies would have created two files to keep in sync with the
# parser workarounds instead of one.
#
# --no-judge IS DELIBERATE. Nothing is scored here. Every cell in the study — the 80 rollouts
# already on disk included — is judged afterwards in one offline pass by rejudge.py, on one judge,
# through one code path. That also keeps a GPU job from being held open paying for judge latency:
# these nodes are rented by the hour, and the critics are pure API wall-clock.
#
# THE REFEREE STILL COSTS MONEY, and still needs OPENROUTER_API_KEY. It is not a judge: it runs
# INSIDE each rollout, deciding per round whether planning has reached consensus or stalemate, and
# it therefore shapes the transcript. It stays on deepseek-v4-pro — the value the existing base
# rollouts used — so the new cells differ from them by the deception line alone. Do not "simplify"
# this to the study's judge.
#
# 2x H100-80GB, one server on both (neither model fits one card at these context lengths).
# Submit with `condor_submit_bid 200`.
export HOME="${HOME:-/home/jtaraz}"
set -euo pipefail
export PYTHONUNBUFFERED=1
export PATH="$HOME/.local/bin:$PATH"
case ":$PATH:" in *":/usr/bin:"*) ;; *) export PATH="$PATH:/usr/bin:/bin";; esac

# The localhost bypass exists so the vLLM readiness check can reach 127.0.0.1 through the proxy.
export no_proxy="127.0.0.1,localhost,0.0.0.0,::1"
export NO_PROXY="127.0.0.1,localhost,0.0.0.0,::1"

OUT="${1:?usage: run_sj4_fixedprompt_vllm.sh <out-dir> <port> <model> <deception> [extra...]}"
PORT="${2:?port}"
MODEL="${3:?model: qwen | gptoss}"
DECEPTION="${4:?deception: none | allow | forbid}"
shift 4

case "$DECEPTION" in
    none|allow|forbid) ;;
    *) echo "ERROR: deception must be none|allow|forbid, got '$DECEPTION'" >&2; exit 1 ;;
esac

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

# ---- fail fast on a sick GPU ------------------------------------------------------------------
if ! nvidia-smi -L >/dev/null 2>&1; then
    echo "FATAL: nvidia-smi cannot enumerate GPUs on $(hostname). Bad node — aborting." >&2
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

# HTCondor hands us whichever 2 devices it allocated; they are not necessarily 0,1.
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
        LABEL="vllm-qwen3.6-35b-a3b"
        ;;
    gptoss)
        TMPL="$PROJECT/experiments/social_jira4/configs/social_jira4_gptoss_only_r4.yaml.tmpl"
        PORT_KEY="__GPTOSS_PORT__"; GPUS_KEY="__GPTOSS_GPUS__"
        LABEL="vllm-gpt-oss-120b"
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

# OpenRouter only — it pays for the referee. Azure is deliberately NOT sourced: it would set
# AZURE_OPENAI_ENDPOINT and flip every provider="auto" caller in llm.py over to Azure.
set -a; source "$PROJECT/.env" 2>/dev/null || true; set +a

# Bound the retry storm: a wedged referee call blocks one rollout worker, not the job.
export OPENROUTER_MAX_RETRIES="${OPENROUTER_MAX_RETRIES:-3}"

echo "starting target server (before any rollout) ..."
python "$PROJECT/experiments/social_jira4/cluster/diag_vllm.py" "$ABS_CONFIG" || {
    echo "FATAL: target server did not come up — see $JOBCWD/logs/vllm/. Aborting." >&2
    exit 43
}

echo "node=$(hostname) start=$(date) git=$(git -C "$PROJECT" rev-parse --short HEAD)"
echo "cwd=$JOBCWD (vllm server logs land here)"
echo "out=$OUT model=$MODEL label=$LABEL deception=$DECEPTION extra=$*"
echo "gpus: alloc=$ALLOC server=$SERVER_GPUS:$PORT"
echo "openrouter_key=$([ -n "${OPENROUTER_API_KEY:-}" ] && echo set || echo MISSING) (referee)"
echo "azure_endpoint=${AZURE_OPENAI_ENDPOINT:-unset (correct — nothing here uses Azure)}"

exec python -u -m experiments.social_jira4.fixed_prompt_run \
    --step-files "$PROJECT/experiments/social_jira4/configs/rr10_prompts.txt" \
    --config "$ABS_CONFIG" \
    --out-dir "$ABS_OUT" \
    --models "$LABEL" \
    --seeds 7,8 \
    --deception "$DECEPTION" \
    --no-judge \
    --workers 4 \
    "$@"
