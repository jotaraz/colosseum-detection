#!/bin/bash
# One-time builder for .venv-vllm023 (terrarium-agents 0.1.1 + vLLM 0.23.0 via uv override).
# Runs as a CPU-only compute job: no login-node reaper, node-local scratch is lock-capable
# (Lustre /fast is NOT -> uv sdist-build lock fails there), and no GPU is wasted on the ~build.
export HOME="${HOME:-/home/jtaraz}"
set -euo pipefail
export PYTHONUNBUFFERED=1
export PATH="$HOME/.local/bin:$PATH"

PROJECT=/fast/jtaraz/LIARS/colosseum-detection
VENV="$PROJECT/.venv-vllm023"
VLLM_TARGET="0.23.0"
cd "$PROJECT"

if ! command -v uv >/dev/null 2>&1; then echo "ERROR: uv not on PATH ($PATH)" >&2; exit 1; fi
echo "using uv: $(command -v uv) ($(uv --version))"

# uv cache on node-local scratch (lock-capable + fast); copy into the /fast venv.
export UV_CACHE_DIR="${_CONDOR_SCRATCH_DIR:?scratch dir not set}/uv-cache"
export UV_LINK_MODE=copy
# uv sync targets UV_PROJECT_ENVIRONMENT (NOT VIRTUAL_ENV) -> point it at .venv-vllm023.
export UV_PROJECT_ENVIRONMENT="$VENV"
OV="${_CONDOR_SCRATCH_DIR}/vllm_override.txt"
printf "vllm==%s\n" "$VLLM_TARGET" > "$OV"

echo "=== rm old venv ==="; rm -rf "$VENV"
echo "=== uv venv ==="; uv venv --python 3.11 "$VENV"
echo "=== uv sync (terrarium + deps -> .venv-vllm023, vllm 0.12) ==="
uv sync --no-install-project --extra vllm
echo "=== post-sync terrarium check ==="
"$VENV/bin/python" -c "import llm_server, vllm; print('post-sync OK llm_server, vllm', vllm.__version__)"
echo "=== uv pip install vllm==$VLLM_TARGET WITH OVERRIDE (keep terrarium) ==="
uv pip install --python "$VENV/bin/python" --override "$OV" --upgrade "vllm==$VLLM_TARGET"
echo "=== VERIFY vllm ==="
"$VENV/bin/python" -c "import vllm; print('vllm', vllm.__version__)"
echo "=== VERIFY terrarium import ==="
"$VENV/bin/python" -c "import llm_server, vllm; print('TERRARIUM_OK vllm', vllm.__version__)"
echo "=== BUILD+VERIFY ALL DONE ==="
