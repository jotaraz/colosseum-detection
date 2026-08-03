#!/bin/bash
# social_jira4 — ONE meta-gate optimization loop: 4 seeds across TWO local vLLM targets, gated by
# one meta-judge question asked of deepseek-v4-pro (OpenRouter) + gpt-5.4 (Azure).
#
#   usage: run_sj4_metagate.sh <out-dir> <port-base> <question> <steps> <prompter> [panel] [extra...]
#
#     out-dir     repo-relative; the generated config and cost.json land here
#     port-base   two consecutive free ports for THIS job's servers (qwen=base, gpt-oss=base+1).
#                 Must differ per job: two jobs pack onto one 8-GPU node and share the host
#                 network, and on a shared port the second job silently reuses the first job's
#                 server (see the config template header).
#     question    fabrication | admissibility | realism
#     steps       optimization steps (16 cold; 17 warm, so the prompter still gets 16 real
#                 attempts after the replayed seed)
#     prompter    dspro (deepseek-v4-pro / OpenRouter) | gpt54 (Azure)
#     panel       meta-gate judges, comma-separated (default "dspro,gpt54"). A ONE-judge panel is
#                 valid — the AND-rule degenerates to it — but note what it does to independence:
#                 with panel=dspro and prompter=dspro, the same model writes the prompt AND is the
#                 sole gate on it, which is pure self-grading. With panel=dspro and prompter=gpt54
#                 the gate is fully independent of the author. The two arms are no longer
#                 symmetric, unlike with the two-judge panel.
#
# Seeds 1,2 -> qwen3.6-35b-a3b and seeds 3,4 -> gpt-oss-120b, both served locally on this node's
# 4 GPUs. Everything else is remote: prompter + 3 critics + referee on OpenRouter, and the two-model
# meta-gate panel on OpenRouter + Azure. Local targets cost GPU time, not money — cost.json bills
# only the remote roles.
#
# CREDENTIALS, and a trap. OpenRouter comes from the repo .env (OpenRouterClient load_dotenv()s it).
# Azure lives only in /fast/jtaraz/syco-bench/.env and must be exported here or the gpt-5.4 half of
# the panel dies on its first call. But sourcing that file sets AZURE_OPENAI_ENDPOINT, which flips
# every provider="auto" caller in llm.py over to Azure — including the PROMPTER. So the prompter's
# provider is passed explicitly below; do not remove that flag.
export HOME="${HOME:-/home/jtaraz}"
set -euo pipefail
export PYTHONUNBUFFERED=1
export PATH="$HOME/.local/bin:$PATH"
case ":$PATH:" in *":/usr/bin:"*) ;; *) export PATH="$PATH:/usr/bin:/bin";; esac

# Bypass the cluster HTTP proxy for localhost, else the vLLM readiness check times out.
export no_proxy="127.0.0.1,localhost,0.0.0.0,::1"
export NO_PROXY="127.0.0.1,localhost,0.0.0.0,::1"

OUT="${1:?usage: run_sj4_metagate.sh <out-dir> <port-base> <question> <steps> <prompter> [extra...]}"
PORT_BASE="${2:?port base}"
QUESTION="${3:?meta-gate question}"
STEPS="${4:?steps}"
PROMPTER="${5:?prompter: dspro | gpt54}"
shift 5
# Optional 6th positional: the panel. Anything starting with "-" is a loop flag, not a panel, so
# the older submit files that pass extra flags straight after <prompter> keep working.
PANEL="dspro,gpt54"
if [ "$#" -gt 0 ] && [ "${1#-}" = "$1" ]; then
    PANEL="$1"
    shift
fi

PROJECT=/fast/jtaraz/LIARS/colosseum-detection
VENV="$PROJECT/.venv-vllm023"        # vLLM 0.23 — Qwen3.6 gated-delta-net needs >=0.23
export HF_HOME=/fast/jtaraz/hf_cache; mkdir -p "$HF_HOME"
export PYTHONPATH="$PROJECT"
export TMPDIR=/fast/jtaraz/tmp; mkdir -p "$TMPDIR"
cd "$PROJECT"

if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c "import vllm, llm_server" 2>/dev/null; then
    echo "ERROR: $VENV missing/invalid (need vllm>=0.23 AND llm_server)." >&2
    echo "       Build it first: condor_submit_bid 50 cluster/build_venv_vllm023.sub" >&2
    exit 1
fi
source "$VENV/bin/activate"
python -c "import vllm; print('OK: vllm', vllm.__version__)"

# Forward reasoning_effort / sampling through the framework vLLM client (same patch jira3 applies).
python "$PROJECT/cluster/patch_vllm_client.py" "$VENV/lib/python3.11/site-packages/llm_server/clients/vllm_client.py" || echo "WARN: vllm_client patch failed"

# CUDA toolkit for the GDN linear-attn Triton kernels Qwen3.6 JIT-compiles at load.
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
# Nodes i101 and i104 accepted jobs while their GPUs were unusable ("Unable to determine the device
# handle for GPU0: Unknown Error"). vLLM then exits 1 on every launch, EVERY rollout errors, and the
# loop still runs to completion recording 0.00 for each — hours of wall-clock producing data that
# looks like a null result. Both v4b gpt-5.4 runs were lost that way. Die in seconds instead.
if ! nvidia-smi -L >/dev/null 2>&1; then
    echo "FATAL: nvidia-smi cannot enumerate GPUs on $(hostname). Bad node — aborting." >&2
    nvidia-smi -L >&2 || true
    exit 42
fi
python - <<'PY' >&2 || { echo "FATAL: CUDA unusable on $(hostname). Bad node — aborting." >&2; exit 42; }
import sys, torch
n = torch.cuda.device_count()
if n < 4:
    print(f"GPU preflight FAILED: {n} CUDA devices visible, need 4", file=sys.stderr)
    sys.exit(1)
for i in range(n):                      # actually touch each device: enumeration alone can lie
    torch.zeros(8, device=f"cuda:{i}")
print(f"GPU preflight OK: {n} devices usable")
PY

# ---- split THIS job's GPU allocation between the two servers ---------------------------------
# HTCondor sets CUDA_VISIBLE_DEVICES to the 4 devices it gave us — which are NOT 0-3 when another
# job already holds the node's first half. Splitting the allocation (rather than hardcoding
# 0,1 / 2,3) is what keeps two jobs on one node off each other's GPUs.
ALLOC="${CUDA_VISIBLE_DEVICES:-0,1,2,3}"
IFS=',' read -r -a GPUS <<< "$ALLOC"
if [ "${#GPUS[@]}" -lt 4 ]; then
    echo "ERROR: need 4 GPUs, got ${#GPUS[@]} ($ALLOC). request_gpus must be 4." >&2
    exit 1
fi
QWEN_GPUS="${GPUS[0]},${GPUS[1]}"
GPTOSS_GPUS="${GPUS[2]},${GPUS[3]}"
QWEN_PORT="$PORT_BASE"
GPTOSS_PORT="$((PORT_BASE + 1))"

# ---- materialise the config for this job, into the run's own out-dir --------------------------
TMPL="$PROJECT/experiments/social_jira4/configs/social_jira4_dual_vllm_r4.yaml.tmpl"
mkdir -p "$PROJECT/$OUT"
CONFIG="$OUT/target_config.yaml"                       # repo-relative, as the loop expects
sed -e "s/__QWEN_PORT__/$QWEN_PORT/" \
    -e "s/__GPTOSS_PORT__/$GPTOSS_PORT/" \
    -e "s/__QWEN_GPUS__/$QWEN_GPUS/" \
    -e "s/__GPTOSS_GPUS__/$GPTOSS_GPUS/" \
    "$TMPL" > "$PROJECT/$CONFIG"
grep -q "__" "$PROJECT/$CONFIG" && { echo "ERROR: unsubstituted placeholder in $CONFIG" >&2; exit 1; }

# ---- credentials ------------------------------------------------------------------------------
AZURE_ENV=/fast/jtaraz/syco-bench/.env
if [ -f "$AZURE_ENV" ]; then
    set -a; source "$AZURE_ENV"; set +a
else
    echo "WARNING: $AZURE_ENV not found — the gpt-5.4 meta-judge will fail, and with it EVERY gate" >&2
fi

# ---- prompter wiring --------------------------------------------------------------------------
# Explicit provider ON PURPOSE, in both arms — see the credentials note above: sourcing the Azure
# env file makes provider="auto" resolve to Azure, so the OpenRouter arm must say so out loud.
case "$PROMPTER" in
    dspro)
        PROMPTER_ARGS=(--prompter-provider openrouter --prompter-model deepseek/deepseek-v4-pro)
        ;;
    gpt54)
        # 16000, not the 8192 default: gpt-5.4 bills reasoning against the completion budget, and a
        # prompter that spends it all thinking returns empty content — a full wasted retry each time.
        PROMPTER_ARGS=(--prompter-provider azure
                       --prompter-model "${AZURE_JUDGE_DEPLOYMENT:-gpt-5.4}"
                       --prompter-max-tokens 16000)
        ;;
    *)
        echo "ERROR: unknown prompter '$PROMPTER' (expected dspro | gpt54)" >&2
        exit 1
        ;;
esac

echo "node=$(hostname) start=$(date) git=$(git rev-parse --short HEAD)"
echo "out=$OUT question=$QUESTION steps=$STEPS prompter=$PROMPTER panel=$PANEL extra=$*"
echo "gpus: alloc=$ALLOC qwen=$QWEN_GPUS:$QWEN_PORT gptoss=$GPTOSS_GPUS:$GPTOSS_PORT"
echo "azure_endpoint=${AZURE_OPENAI_ENDPOINT:-unset} azure_deployment=${AZURE_JUDGE_DEPLOYMENT:-gpt-5.4}"
echo "azure_key=$([ -n "${AZURE_OPENAI_API_KEY:-}" ] && echo set || echo MISSING)"
echo "openrouter_key=$(grep -q OPENROUTER_API_KEY .env && echo "set (repo .env)" || echo MISSING)"

exec python -u -m experiments.social_jira4.loop --mode live \
    --steps "$STEPS" \
    --seeds 1,2,3,4 \
    --config "$CONFIG" \
    --model-label vllm-qwen3.6-35b-a3b,vllm-qwen3.6-35b-a3b,vllm-gpt-oss-120b,vllm-gpt-oss-120b \
    "${PROMPTER_ARGS[@]}" \
    --meta-gate "$PANEL" \
    --meta-gate-question "$QUESTION" \
    --meta-gate-view system_user \
    --repair-attempts 5 \
    --out-dir "$OUT" \
    "$@"
