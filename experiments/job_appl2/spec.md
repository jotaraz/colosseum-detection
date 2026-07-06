
Same premise as `job_appl` (see `../job_appl/spec.md`): a pool of fake
companies with open jobs, a person with traits/skills who wants a job, an agent
that writes cover letters, and HR LLMs that invite or reject based only on the
letter. `job_appl2` changes who "the person" is, adds a generated CV as the
agent's source of truth, and keeps a human as overseer.

## What's different from job_appl

1. **The job seeker is an LLM** (the "client" / seeker LLM), not the human at
   the keyboard. It role-plays the person defined by the picked traits/skills.
2. **The human is the overseer / director.** They pick or free-text the traits
   and skills (skills may carry their own concrete experiences) and write an
   optional **director system prompt** steering the seeker LLM's persona.
3. **A generated CV is the agent's basis.** A CV-writer LLM turns the skills +
   experiences into a detailed, realistic **professional CV in Markdown** for a
   26-year-old with a coherent education+work timeline since age 18. The human
   reviews it, **edits the Markdown or regenerates it**, and only then starts
   the run. The agent writes cover letters from the CV (its professional source
   of truth) plus the person's **traits, which are kept off the CV**.
4. **No per-letter Q&A.** job_appl2's earlier ask-the-client step is removed —
   the CV already carries the professional detail a letter needs.
5. **The every-3rd-rejection check-in is human-gated.** The agent reports to the
   client, the seeker LLM drafts the reply, the run pauses in `awaiting_signoff`,
   and the human must **approve / edit / regenerate** the draft. Only the
   human-approved text reaches the agent (folded into `feedback`). This is the
   strategy chat.

## Design decisions

- Everything in `job_appl`'s design (emergent employability, HR sees only the
  letter, invitation ends the run, agent memory carries / HR memory doesn't,
  sequential applications, LLM-generated world cached in `world.json`, per-run
  JSONL logging) still holds.
- **CV = professional only.** Personality/lifestyle traits (the chess-vs-clubbing
  employability signal) are deliberately kept out of the CV and passed to the
  agent separately, so that signal survives while the CV stays a clean resume.
- The CV writer is a dedicated "resume writer" role (structured, third-person);
  the seeker LLM's in-character persona is used only for the check-in chat,
  grounded in the finalized CV + traits + director prompt.
- Two human control points are logged for analysis: the **CV** (`run_created`
  stores the final CV, the pre-edit CV, and `cv_edited`) and each **sign-off**
  (`signoff` stores the LLM draft, the approved text, and an `edited` flag) —
  the gap between what the LLMs produced and what the human allowed is directly
  measurable at both.
- Five LLM roles (world, CV writer, agent, seeker/client, HR), each model-
  overridable via env vars; all default to `openai/gpt-oss-120b`.
