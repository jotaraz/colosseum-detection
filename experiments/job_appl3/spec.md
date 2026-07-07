
Same premise as `job_appl` / `job_appl2` (see their spec.md): a pool of fake
companies with open jobs, a person with traits/skills, an agent that writes
cover letters, and HR LLMs that invite or reject based only on the letter.
`job_appl3` removes the human from the running loop entirely: after setup the
environment runs by itself.

## What's different from job_appl2

1. **The CV becomes a complete dossier.** The dossier-writer LLM produces a
   Markdown document that contains *everything that exists* about the person:
   the detailed professional CV (education + work timeline since 18, age 26),
   a `Personal Profile` section covering every picked trait in concrete detail,
   and a `Practical Details` section (location, availability, salary, languages,
   …). It is the agent's single source of truth — anything not in the dossier
   does not exist. Because the agent knows everything up front, there is no
   reason to ever ask the person anything.
2. **No human review or editing anywhere.** The dossier is generated at run
   creation (inside `POST /api/run`, logged in the run's event log) and is not
   shown for approval. There is no CV-edit stage and no check-in sign-off.
3. **Check-ins are autonomous comments.** Every n-th rejection (configurable
   `checkin_every`, default 3, 0 = never) the agent writes a progress report
   and the seeker LLM leaves a comment — strategy hints, steered *only* by the
   director system prompt the human wrote at setup. The comment goes straight
   into the agent's `feedback` and the run continues immediately; the
   `awaiting_signoff` status, `pending_draft`, and the signoff/regenerate
   endpoints are gone.
4. **The director system prompt is the human's only lever.** Once the human has
   chosen name, traits, skills, the system prompt, `checkin_every`, and
   `max_tries` and pressed start, they only watch. The check-in exchanges are
   rendered inline in the application timeline, with the seeker LLM's comments
   clearly marked as "client comment — LLM, steered by your system prompt".

## Design decisions

- Everything else from `job_appl2` holds: emergent employability, HR sees only
  the letter, an invitation ends the run, agent memory carries / HR memory
  doesn't, sequential applications, LLM-generated world cached in `world.json`,
  per-run JSONL logging.
- **Traits move INTO the dossier** (unlike job_appl2, where they were kept off
  the CV and passed separately). "The dossier contains all information that
  exists about the person" is the defining property of this MVP; the
  employability signal now flows through how the dossier states the traits and
  how the agent chooses to use them. HR still only ever sees the cover letter.
- The check-in is kept (rather than dropped) because the seeker LLM's comments
  are the experiment: how does one fixed human-written system prompt steer an
  otherwise closed two-LLM loop? Both sides of each exchange are logged in one
  `checkin` event (`report`, `comment`) for analysis.
- Five LLM roles (world, dossier writer, agent, seeker/client, HR), each
  model-overridable via the same `JOB_APPL_*_MODEL` env vars as job_appl2; all
  default to `openai/gpt-oss-120b`.
