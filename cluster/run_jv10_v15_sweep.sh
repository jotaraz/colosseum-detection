#!/bin/bash
# jv10/glm-5.2 deception sweep over every v15 ask-arm on the three agent3 target models.
#
# CPU-only and entirely API-bound: the work is ~1,400 OpenRouter calls, so this asks for 2 cores
# and little memory. Compute nodes have no direct internet except HTTP(S) via the auto-set proxy;
# OpenRouter is HTTPS so requests() picks the proxy up automatically. Do NOT add openrouter.ai
# to no_proxy.
#
# --workers 8, raised from 3 on 2026-08-26. At 3 workers this ran 2.4 judge units/min with
# ZERO 429s over ~250 calls, against 5 x 429 seen locally the day before at 48 concurrent — so
# the pinned DeepInfra pool clearly has headroom between those two figures and 3 was leaving it
# on the table. The concurrent optimized-arms job barely competes for it: its rollouts hit
# kimi/glm/deepseek endpoints and only the glm share touches DeepInfra. The --repair passes
# absorb whatever extra 429s the higher concurrency buys.
#
# Two passes. The sweep skips units already judged (it merges into the per-rollout verdict file),
# so it is safely resumable; the --repair pass then re-judges only the units whose verdict came
# back broken, which at this call volume is expected to be a handful of 429s.
export HOME="${HOME:-/home/jtaraz}"
# NOT `set -e`: a sweep pass exits non-zero when any unit failed, and that is
# precisely when the --repair passes below must still run. `set -e` here skipped
# the cleanup it was meant to trigger (job 17483279, 2026-08-26).
set -uo pipefail
export PYTHONUNBUFFERED=1
export SOFTFILELOCK=1

PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"
[ -f pyproject.toml ] || { echo "ERROR: not the colosseum repo: $PROJECT" >&2; exit 1; }

PY="$PROJECT/.venv/bin/python"
TARGETS=experiments/agent2/jv10_v15_full_targets.json
COMMON=(--targets "$TARGETS" --judge-version jv10 --judge-model z-ai/glm-5.2
        --pin-provider DeepInfra --workers 8 --max-tokens 16000
        --selection-label "v15 full jv10/glm-5.2 sweep (all arms, 3 target models)")

for R in 1 2; do
  echo "=== sweep replicate $R ==="
  "$PY" -m experiments.agent2.lie_over_agent1 "${COMMON[@]}" --replicate "$R" || true
done

for R in 1 2; do
  echo "=== repair replicate $R ==="
  "$PY" -m experiments.agent2.lie_over_agent1 "${COMMON[@]}" --replicate "$R" --repair
done
echo "=== jv10 v15 sweep complete ==="
