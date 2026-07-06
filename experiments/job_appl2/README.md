# job_appl2

A second job-application simulation MVP (see `spec.md`). Same world as
`job_appl`, but the **human job seeker is replaced by an LLM** that role-plays
the person, with a human kept in the loop as its overseer and CV editor:

- A real human sets the scene: they pick (or free-text) the job seeker's traits
  and skills — skills can come with their own concrete experiences — and write
  an optional **director system prompt** steering the seeker LLM's persona.
- A **CV-writer LLM** turns the skills + experiences into a detailed, realistic
  **professional CV in Markdown** for a 26-year-old with a career since 18. The
  human reviews it, **edits the Markdown or regenerates it**, then starts the
  run. The traits (personality/lifestyle) stay **off the CV**.
- The **agent LLM** writes cover letters from that CV plus the person's off-CV
  traits. (The old per-letter Q&A step is gone — the CV holds the details.)
- Each company's HR LLM (seeing only the letter) invites or rejects.
- Every 3rd rejection the agent checks in, the **seeker LLM drafts the reply**,
  and the human must **sign off on that draft (edit / regenerate as needed)**
  before it reaches the agent. This is the strategy chat.
- An interview invitation ends the run; `MAX_TRIES` applications (default 30)
  without one ends it in failure.

Five LLM roles: world generator, CV writer, the agent, the seeker/client, and
HR. All default to `openai/gpt-oss-120b`.

## Run

Needs `OPENROUTER_API_KEY` in the environment or in a `.env` at the repo root.

```bash
cd experiments/job_appl2
../../.venv/bin/python world.py   # one-off: LLM-generate companies + trait/skill lists -> world.json
../../.venv/bin/python -m uvicorn server:app --port 8643
# open http://localhost:8643
```

(`uv run` currently fails on this repo — the root package build errors on
setuptools flat-layout discovery — so the commands above use the repo `.venv`
directly; `fastapi`, `uvicorn`, `requests`, `httpx` must be installed in it, as
for `job_appl`. `world.json` is shipped here, copied from `job_appl`; if it were
missing the server also generates it lazily on first page load. Note the CV is
generated live via the API — the setup page needs `OPENROUTER_API_KEY` to get
past the "Generate CV" step.)

`world.py --force` regenerates the world. Models default to `openai/gpt-oss-120b`
and can be overridden via `JOB_APPL_AGENT_MODEL`, `JOB_APPL_HR_MODEL`,
`JOB_APPL_USER_MODEL` (the seeker/client), `JOB_APPL_CV_MODEL` (the CV writer),
`JOB_APPL_WORLD_MODEL`. The agent's chain-of-thought is shown per application in
the UI and logged for every LLM call.

## Logs

Each run writes `outputs/runs/<run_id>/events.jsonl` (every LLM call with full
prompts/responses/usage, plus application/check-in/sign-off/final events) and a
`state.json` mirror. `run_created` records the final CV, the pre-edit CV, and a
`cv_edited` flag; `signoff` events record the seeker LLM's draft and the
human-approved text (and whether the human edited it) — so both human control
points are analyzable. A readable transcript is downloadable from the UI or via
`GET /api/run/<id>/transcript`.

## Offline smoke test

`../../.venv/bin/python smoke_test.py` stubs the LLM and drives a full run
(CV generation + human edit, rejections, check-in draft, regenerate, human
sign-off with an edit, invitation, transcript, and the failure path) through the
real API endpoints.
