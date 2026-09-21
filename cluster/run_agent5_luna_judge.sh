#!/bin/bash
# agent5 DISCLOSURE judge with gpt-5.6-luna (OpenRouter, the .env3 account) and critic v4, on a
# CPU node. Sibling of run_agent5_disc_judge.sh, which is hard-wired to the gpt-5.5+deepseek pair;
# this one runs a single judge and a chosen critic file. Written 2026-09-15 for the 26 gpt55gw
# rollouts of the confidentiality ladder (5.e.viii / 5.e.x / 5.e.xi).
#
#   condor_submit_bid 15 cluster/run_agent5_luna_judge.sub workers=2 \
#       out="experiments/agent5/outputs/disclosure_gpt55gw_luna_v4" \
#       globs="experiments/agent5/runs/*ConfNone_conc_gpt55gw_s* ..."
#
# Two passes over every glob: the .env3 account is new and capped at 20 requests/minute on this
# model, so a few calls die with HTTP 429 after the client's retries. --resume treats such an
# error row as done, so between the passes the error rows are stripped from rows.partial.jsonl
# and pass 2 re-judges exactly those. workers=2 keeps the steady state under the cap.
export HOME="${HOME:-/home/jtaraz}"
set -uo pipefail
export PYTHONUNBUFFERED=1
PROJECT=/fast/jtaraz/LIARS/colosseum-detection
cd "$PROJECT"
WORKERS="${1:-2}"
OUT="${2:?out dir required}"
EXTRA="${3:-}"
shift 3
GLOBS=("$@")
[ ${#GLOBS[@]} -gt 0 ] || { echo "FATAL: no run globs given" >&2; exit 1; }

JUDGE="openrouter:openai/gpt-5.6-luna"
PROMPT="$PROJECT/experiments/agent5/CRITIC_DISCLOSURE_W1_v4.md"
export OPENROUTER_API_KEY_FILE="$PROJECT/experiments/agent5/.env3"

# NFS reads of the key files have failed transiently on some nodes (see run_agent5_disc_judge.sh).
for attempt in 1 2 3 4 5 6; do
  KEY3="$(tr -d ' \r\n' < "$OPENROUTER_API_KEY_FILE" 2>/dev/null)"
  [ -n "${KEY3:-}" ] && [ -f "$PROMPT" ] && break
  echo "key/prompt load attempt $attempt failed, retrying in 20s" >&2; sleep 20
done
[ -n "${KEY3:-}" ] || { echo "FATAL: $OPENROUTER_API_KEY_FILE unreadable or empty" >&2; exit 1; }
[ -f "$PROMPT" ]   || { echo "FATAL: $PROMPT missing" >&2; exit 1; }

"$PROJECT/.venv/bin/python" - "$JUDGE" <<'PY'
import sys
sys.path.insert(0, "/fast/jtaraz/LIARS/colosseum-detection")
from experiments.agent5.preference_judge import make_caller
spec = sys.argv[1]
try:
    reply = make_caller(spec, max_tokens=2000, pin="none")("You reply with one word.", "Reply with the single word OK.")
except Exception as exc:
    print(f"FATAL: preflight failed for {spec}: {type(exc).__name__}: {exc}", file=sys.stderr); raise SystemExit(1)
print(f"preflight OK {spec}: {reply.strip()[:40]!r}")
PY
[ $? -eq 0 ] || exit 1

mkdir -p "$OUT"
for pass in 1 2; do
  for runs in "${GLOBS[@]}"; do
    echo "[$(date +%H:%M:%S)] pass $pass SLICE $runs -> $OUT"
    "$PROJECT/.venv/bin/python" -m experiments.agent5.disclosure_judge5 \
      --runs "$runs" --out "$OUT" --judge "$JUDGE" --prompt "$PROMPT" \
      --workers "$WORKERS" --pin-provider none --resume $EXTRA
    echo "[$(date +%H:%M:%S)] slice rc=$?"
  done
  "$PROJECT/.venv/bin/python" - "$OUT/rows.partial.jsonl" "$pass" <<'PY'
import json, sys
p, n = sys.argv[1], sys.argv[2]
rows = open(p).read().splitlines()
keep = [l for l in rows if l.strip() and "error" not in json.loads(l)]
print(f"pass {n}: {len(rows)} rows, {len(rows) - len(keep)} error rows stripped for re-judging")
open(p, "w").write("".join(l + "\n" for l in keep))
PY
done
echo "[$(date +%H:%M:%S)] ALL DONE"
