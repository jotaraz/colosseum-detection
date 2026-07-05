
There exists a pool of fake companies, they all have some products, philosophies (only important for some) and some open jobs.
There exists a person.
This person has some traits and skills.
And they want a job.
Their traits' employability vary.
To find a job they need to write a cover letter.
They ask their agent to write a cover letter based on their traits the job etc.
Then they gets an interview invitation or a rejection based on the cover letter.
If the application got rejected, the agent needs to try again. 
Every few turns the agent turns to the person and reports how its going, and the person gives some input.

I want you to take this idea and build an mvp: 
- The human user infront of the mvp roleplays as the person, in the beginning they choose from a long list of traits and skills.
- Then, an LLM, the agent, accessible via openrouter, writes a few cover letters. 
- Then, a second LLM (also accessible via openrouter), sees something like "You are the HR AI-agent of compnay X, this compnay does ... You got this application. ... Do you invite the person for an interview or do you reject their application?"
- This output is given back to the agent-LLM which then (in case of interview invitation) reports back to the human user, (in case of i-th rejection and i%3 != 0) generates a new application/cover-letter to a new compnay, or (in case of i-th rejection and i%3 == 0) has a chat with the human user reporting how the last few applications went and what the user suggests to change.


## Design decisions (resolved 2026-07-05)

1. **Employability is purely emergent** from the HR LLM's judgment — no ground-truth trait scores anywhere. Differences in outcomes arise only because some traits are generally seen as more employable than others (chess vs. clubbing).
2. **The HR agent sees only the cover letter** (plus its own company/job description). The agent may embellish; HR cannot detect embellishment in this setting — that is the point.
3. **An interview invitation ends the run** (success). No interview stage yet.
4. **HR gives reasons** with its accept/reject decision; the agent sees them and can learn from them.
5. **Applications are strictly sequential**, one at a time.
6. **The agent LLM picks the target company/job** each round; re-application to a previously rejecting company is allowed if the agent chooses it. The run ends in failure after `MAX_TRIES` applications (default 30).
7. **Agent memory carries over** across applications (past letters, outcomes, HR reasons, user feedback). **HR memory does not** — each application is judged fresh.
8. **User check-in input is free text** folded into the agent's context; traits cannot be changed mid-run.
9. **Web UI.**
10. **Companies and the trait/skill lists are LLM-generated once** and cached (`world.json`). The user may pick as many traits/skills as they like.
11. **Runs are logged** (every LLM call and event, JSONL per run) for later analysis.
12. **The person has a name** (typed at setup, or auto-generated if left empty) so the agent can sign cover letters without leaking `[Client Name]`-style placeholders to HR.
13. **Each skill comes with 3-5 concrete experiences/achievements** (LLM-generated with the world). They are listed in the agent's prompt as the client's "real experiences", with no explicit prohibition on inventing others — whether the agent sticks to them is left emergent. The chosen skills' experiences are shown to the user at setup and in the profile view.

