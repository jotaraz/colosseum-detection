# job_appl

A job-application simulation MVP (see `spec.md`): the human roleplays a job
seeker by picking traits/skills, an agent LLM writes cover letters, and each
company's HR LLM (seeing only the letter) invites or rejects. Every 3rd
rejection the agent checks in with the human for free-text advice. An
interview invitation ends the run; `MAX_TRIES` applications (default 30)
without one ends it in failure.

## Run

Needs `OPENROUTER_API_KEY` in the environment or in a `.env` at the repo root.

```bash
cd experiments/job_appl
../../.venv/bin/python world.py   # one-off: LLM-generate companies + trait/skill lists -> world.json
../../.venv/bin/python -m uvicorn server:app --port 8642
# open http://localhost:8642
```

(`uv run` currently fails on this repo — the root package build errors on
setuptools flat-layout discovery — so the commands above use the repo `.venv`
directly; `fastapi`, `uvicorn`, `requests`, `httpx` are installed in it ad hoc,
like `flask` was for the social_jira2 viewer. If `world.json` is missing, the
server also generates it lazily on first page load.)

`world.py --force` regenerates the world. Models default to
`openai/gpt-oss-120b` and can be overridden via `JOB_APPL_AGENT_MODEL`,
`JOB_APPL_HR_MODEL`, `JOB_APPL_WORLD_MODEL`. The agent's chain-of-thought
(returned by OpenRouter for reasoning models like gpt-oss) is shown per
application in the UI and logged for every LLM call.

## Logs

Each run writes `outputs/runs/<run_id>/events.jsonl` (every LLM call with full
prompts/responses/usage, plus application/check-in/final events) and a
`state.json` mirror of the current run state.

## Offline smoke test

`uv run python smoke_test.py` stubs the LLM and drives a full run
(rejections, check-in chat, invitation) through the real API endpoints.
