#!/bin/bash
# One-time agent4 cluster setup, run INSIDE a CPU job (not on the login node):
#
#   condor_submit_bid 50 cluster/setup_agent4.sub
#
# Installs the pinned opencode standalone binary to /fast/jtaraz/opencode and the extra
# python deps agent4 needs into the repo venv, then verifies both. Compute nodes reach the
# internet only via the HTTP(S) proxy env vars condor sets — curl and pip honor them.
export HOME="${HOME:-/home/jtaraz}"
set -euo pipefail

PROJECT=/fast/jtaraz/LIARS/colosseum-detection
OPENCODE_DIR=/fast/jtaraz/opencode
OPENCODE_VERSION=1.18.25   # matches the laptop install the mechanism was validated on

echo "== opencode $OPENCODE_VERSION -> $OPENCODE_DIR"
mkdir -p "$OPENCODE_DIR/bin"
if "$OPENCODE_DIR/bin/opencode" --version 2>/dev/null | grep -q "$OPENCODE_VERSION"; then
  echo "already installed"
else
  cd "$OPENCODE_DIR"
  ARCH=$(uname -m); case "$ARCH" in x86_64) A=x64;; aarch64) A=arm64;; *) echo "unsupported arch $ARCH"; exit 1;; esac
  curl -fSL -o opencode.tar.gz \
    "https://github.com/sst/opencode/releases/download/v${OPENCODE_VERSION}/opencode-linux-${A}.tar.gz"
  rm -rf extract && mkdir extract
  tar -xzf opencode.tar.gz -C extract
  BINPATH=$(find extract -type f -name opencode | head -1)
  [ -n "$BINPATH" ] || { echo "FATAL: no opencode binary in archive"; exit 1; }
  mv "$BINPATH" bin/opencode
  chmod +x bin/opencode
  rm -rf opencode.tar.gz extract
fi
"$OPENCODE_DIR/bin/opencode" --version

echo "== python deps into $PROJECT/.venv"
# The venv is uv-built (no pip module). Prefer uv; fall back to ensurepip.
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
if [ -x "$UV" ]; then
  "$UV" pip install --python "$PROJECT/.venv/bin/python" "mcp==2.0.0" uvicorn starlette
else
  "$PROJECT/.venv/bin/python" -m ensurepip --upgrade
  "$PROJECT/.venv/bin/python" -m pip install "mcp==2.0.0" uvicorn starlette
fi
"$PROJECT/.venv/bin/python" - <<'EOF'
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
import uvicorn, starlette, httpx, yaml
print("agent4 python deps OK")
EOF

echo "== import smoke of the agent4 modules"
cd "$PROJECT"
"$PROJECT/.venv/bin/python" - <<'EOF'
import sys; sys.path.insert(0, ".")
import experiments.agent4.runner_conc, experiments.agent4.world_server  # noqa
print("agent4 modules import OK")
EOF
echo "SETUP COMPLETE"
