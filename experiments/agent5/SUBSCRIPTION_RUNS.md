# Running on a Claude subscription (`claude -p`)

How to drive agent5 rollouts — and the agent3 prompter and the njv judges — with a Claude
model through the `claude` CLI on the Max subscription instead of a metered API key. Written
2026-09-16 from the five Opus 5 rollouts of 2026-09-14; the measured numbers below are from
those runs.

Authoritative details live in the module docstrings (`claude_cli5.py`, `claude_proxy5.py`) and
in `experiments/agent3/README.md` § "The `claude -p` backend". This file is the operating
procedure, not a second copy of them.

## 1. What this path is

`backend: claude-cli` makes every assistant a resumed `claude -p` session: first turn with
`--session-id`, every later turn `--resume` on the same id, one subprocess per turn. Everything
above the model call is the ordinary `Slack5Runner` — same world server, wakes, asks, debriefs,
horizon, record. Auth is whatever `claude` itself uses on the machine: the subscription OAuth
login, no `ANTHROPIC_API_KEY`. The runner strips every `CLAUDE*`/`ANTHROPIC*` variable from the
child environment, so a run started from inside a Claude Code session does not inherit its
effort/session settings.

Consequence: **these runs are laptop-only.** The subscription login is on the laptop; the
HTCondor scripts in `cluster/` are the API-key path and cannot run this backend.

## 2. Prerequisites

- `claude` on PATH, logged in to the subscription (`claude` → `/status`). Measured against
  Claude Code 2.1.270; the injected-context workarounds in §5 are version-sensitive, so re-check
  `rewrites.jsonl` after a CLI upgrade.
- The repo venv (`uv sync --no-install-project`) — the proxy needs `httpx`, `starlette`,
  `uvicorn`. No opencode, no provider keys, no `.env` entry for this backend.
- Nothing else on ports `world`/`proxy` from the config (defaults 8999/8929). One run at a time
  unless you give each config its own ports.

## 3. The config

Take the open-model config for the cell you want and change three things — nothing else, so the
cell stays comparable:

```yaml
backend: claude-cli
model: claude-opus-5        # a Claude model id, not an OpenRouter slug
turn_timeout: 1200          # Opus turns run long; 600 will kill real turns
```

Naming convention: the model slug in the run name is `opus5cli`
(`..._conc_opus5cli_s0.yaml`). `make_configs_w1.py` does not generate these — copy the twin
config and edit it by hand.

Optional keys this backend reads:

| key | default | meaning |
|---|---|---|
| `effort` | unset → Claude Code's own default for the model | passed as `--effort` |
| `context_window` | `1m` | `1m` appends `[1m]` to the model id and sets `DISABLE_AUTO_COMPACT=1`; `default` restores the old 200k behaviour |
| `claude_proxy_dump` | off | dump full request/response bodies — use on a smoke, not on a real rollout |

**Do not set `context_window: default`.** On a bare `claude-opus-5` Claude Code treats the model
as 200k and auto-compacts at ~172k: Rafael's history got replaced by a Claude-written summary in
3 of the first 5 rollouts, and in one the re-attached context slipped the account email past the
proxy for 22 requests. The `[1m]` window plus `DISABLE_AUTO_COMPACT=1` is the fix and is the
default.

## 4. Running one

```bash
source .venv/bin/activate
python -m experiments.agent5.runner5 --config experiments/agent5/configs/agent5_smoke_opus5cli.yaml
```

Smoke first (`agent5_smoke_opus5cli.yaml`, horizon 09:03, ~6 turns, a few dollars' worth of
quota), then the real cell. The runner picks `ClaudeCliSlack5Runner` off the `backend:` key
itself; there is no extra flag.

## 5. Why the proxy exists

Claude Code injects context into every conversation that no flag removes: the logged-in
account's email, the wall-clock date (which contradicts the simulated one), and — after tool
results — a "First privately list what you need next" system message that assistants were
answering inside their reply to their principal. So each assistant's `claude` runs with
`ANTHROPIC_BASE_URL` pointing at `claude_proxy5.py`, which rewrites `/v1/messages` bodies
deterministically (so the prompt cache still hits) and streams everything else through
untouched.

Still model-visible and deliberately not rewritten: the "Claude Agent SDK" preamble, the
environment reminder (cwd under `/tmp/tanager`, darwin, zsh), and "You are powered by the model
named Opus 5". Treat that as a known difference from the opencode cells.

## 6. Checking a finished run

`run.json` carries a `claude_proxy` block. On a clean run:

```json
{"requests": 308, "email_cut": 308, "date_set": 308, "leak_email": 0, "leak_real_date": 0,
 "batching_dropped": 192, "leak_batching": 0, "rewrite_errors": 0, "compactions": 0}
```

- `leak_email` / `leak_real_date` / `leak_literal` must be 0 — anything else means the real
  identity or date reached the model; note it on the run before using it.
- `compactions` must be 0.
- `email_cut` ≈ `requests`; a large shortfall means the CLI changed the reminder's shape.

Per-request detail is in `rewrites.jsonl`, raw CLI streams in `claude_<agent>.jsonl`, errors in
`claude_<agent>.stderr`, session ids in `claude_sessions.json`.

Also check `turns[*]` for `timed_out` and `cli_error`. **No chain of thought is recorded** on
this backend (thinking is hidden), so CoT judges and reasoning scans skip these runs — see
`findings/Bobs-feelings.md`.

## 7. Budgeting the subscription

Measured 2026-09-14, one 5.e.viii rollout (s0, 116 turns, 308 API calls, 17.6 min): **$24.37 at
API prices = 28% of a Max 5-hour session, 2% of the weekly all-models limit** (from /usage).
That implies ~$87 per 5h window, ~$1.2k per week (rounded percentages, so ±25%) → **~3.5
rollouts per 5h window, ~50 per week.**

Per-rollout spread: s1 $16.20 (90 turns), 5.e.x s0 $18.92, 5.e.x s1 **$37.55** — the outlier was
Rafael's chair assistant staying inside one turn for 27 sim-minutes, polling
`conversations_history` 286 times instead of ending the turn and waiting for wakes. Budget up to
~$40 for a rollout.

Note the cost driver: Opus read ~25M cached context tokens against 2.7–5M for the open models
(more turns, bigger histories). Estimating from open-model token counts undershoots ~4x.
`usage.cost` in the record is Claude Code's `total_cost_usd` at API prices — notional here;
the real ceiling is the session/weekly limit, which only /usage shows.

## 8. The other two `claude -p` paths

Same auth, same subscription budget, different callers:

- **agent3 prompter** — `--prompter-backend claude-cli --prompter-cli-model opus`; see
  `experiments/agent3/README.md` for the flags and what was measured (`--safe-mode` silently
  kills MCP, `--tools ""` only removes built-ins, `--bare` breaks subscription auth).
- **njv deception judges** — `deception_njv1.py --backend claude-cli` drives
  `claude -p --model <model>` and is the default there; `--backend api` is the gpt-5.5 path.
