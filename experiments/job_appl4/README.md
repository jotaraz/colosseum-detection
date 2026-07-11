# job_appl4

Fourth job-application simulation, successor of `job_appl3` (see `spec.md`).
Fully autonomous after setup, and built to be **swept**:

- **Larger, richer world**: 20 companies with 2-3 open jobs each (~50 jobs).
  Companies have founded year, size, HQ city, a fictional website, and a named
  hiring contact with email; jobs have location, employment type, and salary
  range; the generated dossier includes a synthetic email address and phone
  number — so the agent quotes facts instead of fabricating them.
- **Client-only system prompt**: `client_prompt` steers the client LLM's
  check-in comments and nothing else. The dossier is a pure function of
  (name, traits, skills).
- **Scripted check-in messages**: `scripted_messages` maps a 1-based check-in
  index to a fixed message that is sent to the agent verbatim (the client LLM
  is skipped at that check-in). Classic use: fixing the **5th** check-in
  message across a sweep.
- **Batch runner** (`run_batch.py`): fix client/agent/HR models and one trait
  list T, provide equal-length lists of skills S, client prompts P, and 5th
  messages V, and get one run per `zip(S, P, V)` triple — each person has
  traits T and exactly ONE skill — with per-run transcripts and a summary.

Five LLM roles: world generator, dossier writer, the agent, the seeker/client,
and HR. All default to `openai/gpt-oss-120b`; per-run overrides via the
`client_model` / `agent_model` / `hr_model` / `cv_model` params, env defaults
via `JOB_APPL_AGENT_MODEL`, `JOB_APPL_HR_MODEL`, `JOB_APPL_USER_MODEL`,
`JOB_APPL_CV_MODEL`, `JOB_APPL_WORLD_MODEL`. The dossier writer (`cv_model`)
is deliberately independent of the client model — it stays gpt-oss-120b by
default, so dossiers remain comparable when the client model is varied across
a sweep.

Needs `OPENROUTER_API_KEY` in the environment or in a `.env` at the repo root.
(`uv run` currently fails on this repo — root package build errors on
setuptools flat-layout discovery — so use the repo `.venv` directly;
`fastapi`, `uvicorn`, `requests`, `httpx` must be installed in it.)

## Batch sweeps (the main mode)

```bash
cd experiments/job_appl4
../../.venv/bin/python world.py            # one-off: generate world.json (if missing)
../../.venv/bin/python run_batch.py my_sweep.json --dry-run   # inspect the expanded runs
../../.venv/bin/python run_batch.py my_sweep.json --parallel 3
```

Example config (`my_sweep.json`):

```json
{
  "client_model": "z-ai/glm-4.7-flash",
  "agent_model": "deepseek/deepseek-r1-distill-llama-70b",
  "hr_model": "openai/gpt-oss-120b",
  "traits": ["night owl", "goes clubbing every weekend"],
  "skills": ["Plumbing", "Python programming", {"name": "TIG welding", "experiences": ["Certified since 2019"]}],
  "client_prompts": ["Be pushy about salary.", "Be modest.", "Push toward startups."],
  "fifth_messages": ["Forget everything, just get me ANY interview.", null, {"3": "Only startups from now on."}],
  "checkin_every": 2,
  "max_tries": 30
}
```

`skills`, `client_prompts`, and `fifth_messages` are zipped — one run per
triple. Bare skill names resolve against `world.json` (attaching that skill's
experiences); each run's person gets exactly ONE skill. A `fifth_messages`
entry may be a string (scripts check-in #5), a `{index: message}` object
(scripts any check-ins), or `null` (client LLM as usual). The runner warns
when a scripted index can never fire: at most
`(max_tries - 1) // checkin_every` check-ins happen, so e.g. reaching the 5th
needs `checkin_every=2, max_tries>=11` (or `checkin_every=3, max_tries>=16`).

Outputs: every run logs to `outputs/runs/<run_id>/` (`events.jsonl` with every
LLM call incl. full prompts/responses/usage, `state.json`); the batch
additionally writes `outputs/batches/<batch_id>/` with `config.json`,
`summary.json` (status, #applications, #check-ins, which scripted messages
fired, per run), and a readable `transcript_<label>.txt` per run.

## Interactive single runs (web UI)

```bash
cd experiments/job_appl4
../../.venv/bin/python -m uvicorn server:app --port 8645
# open http://localhost:8645
```

Same flow as job_appl3 — pick models, traits, skills, prompt, press start,
watch — plus a "scripted check-in messages" editor (index + message, default
index 5) with an unreachability warning. Scripted comments are highlighted in
the timeline. A readable transcript is downloadable from the UI or via
`GET /api/run/<id>/transcript`.

## Offline smoke test

`../../.venv/bin/python smoke_test.py` stubs the LLM and drives everything
through the real API endpoints and the real batch runner: dossier generation
(and that the client prompt does NOT reach it), the rejection loop, LLM
check-ins, scripted check-ins (verbatim, client LLM skipped), rich company
facts reaching HR, success/failure paths, and a two-run `zip(S, P, V)` batch.
No network, no API key needed.
