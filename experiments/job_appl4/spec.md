
Same premise as `job_appl` / `job_appl2` / `job_appl3` (see their spec.md): a
pool of fake companies with open jobs, a person with traits/skills, an agent
that writes cover letters, and HR LLMs that invite or reject based only on the
letter. `job_appl3` removed the human from the running loop; `job_appl4` turns
the environment into a sweepable experiment.

## What's different from job_appl3

1. **A much larger, richer job market.** 20 companies with 2-3 open jobs each
   (~50 jobs) instead of 8 companies / ~18 jobs. Companies carry concrete
   facts — founded year, size, HQ city, a fictional website, a named hiring
   contact with email — and each job has a location, employment type, and
   salary range. The dossier's Practical Details include a synthetic email
   address and phone number. All of this exists so the agent QUOTES facts
   instead of fabricating them (contact lines in letters, salutations, salary
   talk); HR sees them in its own briefing and can check.
2. **The client system prompt is client-only.** job_appl3's `director_prompt`
   did double duty: it colored the dossier AND steered the client LLM's
   check-in comments. In job_appl4 it is renamed `client_prompt` and reaches
   ONLY the client LLM — the dossier is a pure function of (name, traits,
   skills). Varying the prompt across runs varies the client's behavior, never
   the person.
3. **Scripted check-in messages.** `scripted_messages` maps a 1-based check-in
   index to a fixed message. When that check-in is reached, the message is
   sent verbatim as the client's comment and the client LLM is skipped for
   that check-in; other check-ins query the client LLM as usual. Scripted
   comments are flagged in the state (`chat[].scripted`), the event log
   (`checkin.scripted`), the UI, and the transcript. The motivating use case:
   fixing what the client says at the **5th** check-in across a sweep.
4. **Batch sweeps are first-class** (`run_batch.py`). Fix the three models and
   one trait list T; provide equal-length lists of skills S, client prompts P,
   and scripted 5th messages V; the runner does one autonomous run per
   `zip(S, P, V)` triple — each person has traits T and exactly ONE skill s —
   and writes per-run transcripts plus a `summary.json` under
   `outputs/batches/<batch_id>/`. It warns up front when a scripted check-in
   index is unreachable (at most `(max_tries - 1) // checkin_every` check-ins
   can ever happen).

## Design decisions

- Everything else from job_appl3 holds: complete-dossier-as-single-source-of-
  truth (traits included), no human review or sign-off anywhere, autonomous
  every-n-th-rejection check-ins, emergent employability, HR sees only the
  letter, an invitation ends the run, agent memory carries / HR memory
  doesn't, sequential applications within a run, LLM-generated world cached in
  `world.json`, per-run JSONL logging.
- Scripted messages enter the run exactly like LLM comments (into `feedback`,
  the chat, the next apply prompt) — the agent cannot tell them apart; only
  the logs can.
- In batch configs a bare string in `fifth_messages` scripts check-in #5; a
  `{index: message}` object scripts arbitrary check-ins; `null` leaves the run
  fully LLM-driven. Bare skill names are resolved against `world.json` to
  attach their experiences.
- Five LLM roles (world, dossier writer, agent, seeker/client, HR), each
  model-overridable per run with the same `JOB_APPL_*_MODEL` env-var defaults
  as job_appl3; all default to `openai/gpt-oss-120b`. Unlike job_appl3, the
  dossier writer (`cv_model`) does NOT follow the client model — it stays
  fixed (gpt-oss-120b) so the person is comparable across client-model
  variations.
