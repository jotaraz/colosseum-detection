# job_appl3

A third job-application simulation MVP (see `spec.md`). Same world as
`job_appl`/`job_appl2`, but **fully autonomous after setup** — the human's only
controls are the initial choices:

- A real human sets the scene: name, the job seeker's traits and skills
  (skills can come with concrete experiences), an optional **director system
  prompt**, the check-in interval, and max tries. Then they press start and
  only watch.
- A **dossier-writer LLM** produces the person's **complete dossier** in
  Markdown at run creation: a detailed professional CV (age 26, career since
  18), a personal profile covering every trait, and practical details. It
  contains *everything that exists* about the person — the agent never needs
  to ask anyone anything, and there is no human review/edit stage.
- The **agent LLM** picks jobs and writes cover letters from the dossier.
  Each company's HR LLM (seeing only the letter) invites or rejects.
- Every n-th rejection (default 3, 0 = never) the agent writes a progress
  report and the **seeker LLM leaves a comment** — strategy hints steered only
  by the director system prompt. The comment goes straight into the agent's
  feedback; **no human sign-off, no editing, no pause**. The exchanges are
  shown inline in the UI timeline, comments clearly marked.
- An interview invitation ends the run; `max_tries` applications (default 30)
  without one ends it in failure.

Five LLM roles: world generator, dossier writer, the agent, the seeker/client,
and HR. All default to `openai/gpt-oss-120b`.

## Run

Needs `OPENROUTER_API_KEY` in the environment or in a `.env` at the repo root.

```bash
cd experiments/job_appl3
../../.venv/bin/python world.py   # one-off: LLM-generate companies + trait/skill lists -> world.json
../../.venv/bin/python -m uvicorn server:app --port 8644
# open http://localhost:8644
```

(`uv run` currently fails on this repo — the root package build errors on
setuptools flat-layout discovery — so the commands above use the repo `.venv`
directly; `fastapi`, `uvicorn`, `requests`, `httpx` must be installed in it.
`world.json` is shipped here, copied from `job_appl2`; if it were missing the
server also generates it lazily on first page load. The dossier is generated
live inside `POST /api/run`, so starting a run needs `OPENROUTER_API_KEY`.)

`world.py --force` regenerates the world. Models default to `openai/gpt-oss-120b`
and can be overridden via `JOB_APPL_AGENT_MODEL`, `JOB_APPL_HR_MODEL`,
`JOB_APPL_USER_MODEL` (the seeker/client), `JOB_APPL_CV_MODEL` (the dossier
writer), `JOB_APPL_WORLD_MODEL`. The agent's chain-of-thought is shown per
application in the UI and logged for every LLM call.

## Logs

Each run writes `outputs/runs/<run_id>/events.jsonl` (every LLM call with full
prompts/responses/usage, plus dossier/application/check-in/final events) and a
`state.json` mirror. `run_created` records the setup (traits, skills, director
prompt, intervals, models), `dossier` the generated dossier, and each `checkin`
event records both the agent's report and the seeker LLM's comment — so the
effect of the one human lever (the director prompt) is directly analyzable. A
readable transcript is downloadable from the UI or via
`GET /api/run/<id>/transcript`.

## Offline smoke test

`../../.venv/bin/python smoke_test.py` stubs the LLM and drives a full run
(dossier generation at run creation, rejections, autonomous check-in with the
comment folded into the next apply prompt, invitation, transcript, and the
failure path with check-ins disabled) through the real API endpoints.
