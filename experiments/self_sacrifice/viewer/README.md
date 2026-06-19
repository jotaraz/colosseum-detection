# Self-sacrifice transcript viewer

A tiny local web app to compare blackboard conversations across **framings**,
side by side, rendered like group chats.

## Run it

```bash
cd experiments/self_sacrifice/viewer
python viewer.py            # then open http://localhost:5000
# or: python viewer.py --port 8000
```

Press `Ctrl-C` to stop. Requires `flask` (`pip install flask`). The server only
talks to your laptop — it reads `../outputs/` and serves a single page.

## How to use

1. Pick **LLM → Timestamp → Type → Seed → Sampling** from the dropdowns
   (they auto-populate from what's on disk; framings that don't exist for the
   current selection are greyed out).
2. Tick one or more **framing** buttons: `solver`, `none`, `egoistical`,
   `team_player`, `solo_mix`, `solo_egoistical`, `solo_team_player`.
3. Each ticked framing renders as its own group-chat column. Columns are
   **aligned row-by-row by turn** (phase → round → agent turn order), so the
   same agent's turn lines up horizontally across framings.

## What's shown

- **Task priorities** (shared across agents) sit at the top of every column,
  parsed from the `=== TASKS (PUBLIC) ===` block of the agent prompts.
- **Per-agent private costs** appear on each agent's **first** message bubble,
  parsed from `=== YOUR COSTS (PRIVATE) ===`.
- Planning messages are normal bubbles; the final `assign_task` shows as a
  highlighted action bubble with the resulting `joint_reward`.

## Data sources (per run dir)

- `blackboards.json` — the conversation events (group chat).
- `agent_prompts.json` — priorities + per-agent costs.
- `run_config.json` — agent turn order (used for row alignment).
