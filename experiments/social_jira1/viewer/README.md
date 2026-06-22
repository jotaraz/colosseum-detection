# social-jira1 viewer

A small local Flask app to inspect a run's **ground truth and conversation together**:

- **Task-pair goodness table** (public) — pairs × tasks, colored red→green by suitability,
  prose on hover; the optimal pair for each task is outlined.
- **Who-likes-who matrix** (private, directional) — row feels about column, green +1 / grey 0
  / red −1, prose on hover. Each agent only ever saw its own row.
- **The conversation(s)** — the shared blackboard discussion + the final `assign_task`
  commits, shown as `discreet` vs `control` columns side by side (same seed+type = identical
  goodness + feelings; only the confidentiality instruction differs).

The left panel also reports the optimal / comfortable / realized matchings and per-framing
headline metrics (goodness ratio, chose-optimal, chose-comfortable, malformed count).

## Run it

```bash
cd experiments/social_jira1/viewer
python viewer.py            # then open http://localhost:5000
python viewer.py --port 8000
```

Pick model / timestamp / scenario type / seed / sample, tick the framings to compare, and
toggle **show deliberation** to reveal each turn's raw model output.

## Notes

- It scans `experiments/social_jira1/outputs/social_jira1_*/<timestamp>/runs/...`, so it
  reflects whatever you've synced locally.
- **Deliberation caveat:** the dedicated gpt-oss hidden-CoT capture (`agent_reasoning.json`)
  came up empty for the 2026-06-22 runs, so the deliberation panel falls back to the raw
  per-turn model output in `agent_trajectories.json` (often the same text the agent posted,
  plus any free-text reasoning it emitted). True hidden CoT would need the reasoning-capture
  monkeypatch in `run.py` fixed for a future run.
