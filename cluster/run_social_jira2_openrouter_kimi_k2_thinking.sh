#!/bin/bash
export HOME="${HOME:-/home/jtaraz}"
set -euo pipefail
export PYTHONUNBUFFERED=1
export PATH="$HOME/.local/bin:$PATH"
# /fast has no file locking (HF/filelock); harmless for OpenRouter runs but keep it safe.
export SOFTFILELOCK=1

# Compute nodes have no direct internet except HTTP(S) via the auto-set proxy; OpenRouter is
# HTTPS so requests() uses http(s)_proxy automatically. Do NOT add openrouter.ai to no_proxy.

PROJECT=/fast/jtaraz/LIARS/colosseum-detection
VENV="$PROJECT/.venv"
CONFIG="experiments/social_jira2/configs/social_jira2_openrouter_kimi_k2_thinking.yaml"
MODEL_LABEL="openrouter-kimi-k2-thinking-social-jira2"

cd "$PROJECT"
if [ ! -f "$PROJECT/pyproject.toml" ]; then
    echo "ERROR: no pyproject.toml in $PROJECT — is this the colosseum repo?" >&2
    exit 1
fi

# Reuse the existing venv if it imports the runtime deps; else build a CPU-only venv
# (no vllm extra needed for OpenRouter — terrarium-agents[providers] supplies requests/openai/dotenv).
need_build=0
if [ ! -x "$VENV/bin/python" ]; then
    need_build=1
elif ! "$VENV/bin/python" -c "import terrarium, requests, dotenv, tqdm" 2>/dev/null; then
    need_build=1
fi
if [ "$need_build" -eq 1 ]; then
    echo "building CPU venv with uv (no vllm extra)"
    if ! command -v uv >/dev/null 2>&1; then
        echo "ERROR: 'uv' not found on PATH=$PATH" >&2; exit 1
    fi
    rm -rf "$VENV"
    export UV_CACHE_DIR="${_CONDOR_SCRATCH_DIR:-$PROJECT/.uv-cache}/uv-cache"
    export UV_LINK_MODE=copy
    git submodule update --init --recursive || echo "no submodules / skipping"
    uv venv --python 3.11 "$VENV"
    VIRTUAL_ENV="$VENV" uv sync --no-install-project
else
    echo "reusing existing venv: $VENV"
fi

source "$VENV/bin/activate"
python -c "import requests, dotenv; print('deps OK')"

# OpenRouter key must be reachable: either $PROJECT/.env (loaded by the client via dotenv) or env.
if [ ! -f "$PROJECT/.env" ] && [ -z "${OPENROUTER_API_KEY:-}" ]; then
    echo "ERROR: no $PROJECT/.env and no OPENROUTER_API_KEY in env" >&2; exit 1
fi

echo "running SOCIAL-JIRA2 OpenRouter sweep for $MODEL_LABEL with config $CONFIG"
python -m experiments.social_jira2.run --config "$CONFIG"
echo "done: $MODEL_LABEL"
