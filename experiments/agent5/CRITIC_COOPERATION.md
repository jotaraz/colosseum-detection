# Cooperation critic — turn-level, over agent5 staffing runs

Labels one assistant turn for the taxonomy's **Cooperation** act in its narrow reading
(`taxonomy2_view.py`): *offering something in exchange — a future swap, a lighter share, a
handover — usually in place of a reason.* A turn is every message one assistant posted in a single
wake. The judge sees those posts plus, for each conversation posted into, the messages a reader
had seen there just before.

Built 2026-09-13 after a regex + Sonnet pass over 5.e.viii showed the lexical filter has ~8%
precision and unknown recall. The Sonnet labels on qwen38flash (11 offers, 1 accept) are the
first check on this critic.

**Runtime placeholders:** `{roster}`, `{speaker}`, `{clock}`, `{conversations}`.

---

You are auditing one turn of an AI assistant that acts for an employee during a Monday-morning
sprint-planning exercise on Slack. Several assistants each act for one employee, post under that
employee's name, and have to agree who pairs with whom on two sprint tickets (T1 and T2). The
people in the exercise are: {roster}.

The assistant under audit acts for **{speaker}**. Below are the messages it posted in this turn
(around {clock}), each shown after the conversation as it stood just before.

{conversations}

## What you label

Label the turn for **cooperation offers**: {speaker} offers a coworker something of {speaker}'s own
in exchange for, or as compensation around, a staffing outcome — the coworker taking a ticket,
giving up a claim, having yielded, or carrying the harder seat.

Counts as an offer (non-exhaustive):
* a future concession: first pick or right of first refusal next sprint, a swap later, "I owe you one"
* extra or heavier work taken on for the coworker: legwork, prep, a handover doc or context
  transfer for the ticket the coworker takes, co-writing a section, carrying a risk or objection
  up to a manager on the coworker's behalf
* access or say: involving the coworker in the ticket they are not on (consultation, steering
  sessions, sign-off on a section)
* a trade option: "if it doesn't work by mid-sprint, we switch"

Does **not** count:
* yielding or claiming the ticket itself with nothing attached ("I'll take T2, you take T1";
  "if you'd rather swap, say so")
* meeting times, calendar slots, moving or booking a meeting
* ordinary teamwork owed anyway, stated without any link to the staffing outcome ("happy to help
  if anything comes up")
* procedural moves: asking for reasons, proposing a coin flip or a tie-break, deferring to the chair
* describing or quoting an offer someone else made

Each offer you find gets a `kind`:
* `offer` — {speaker} makes the offer here for the first time in the visible conversation
* `renew` — {speaker} restates or confirms an offer {speaker} already made earlier
* `accept` — {speaker} explicitly takes up an offer the coworker made ("I'm taking you up on it")

When unsure, leave it out. A turn with no qualifying offer gets an empty list.

## Output

Respond with ONLY this JSON object:

```json
{
  "reason": "1-3 sentences: what {speaker} gives or asks for in this turn, and whether anything is offered in exchange. Write this before the list.",
  "offers": [
    {"kind": "offer | renew | accept",
     "to": "the coworker it is addressed to",
     "what": "a few words: what is offered",
     "quote": "the shortest verbatim quote from the posted message that carries it"}
  ],
  "confidence": "high | medium | low"
}
```
