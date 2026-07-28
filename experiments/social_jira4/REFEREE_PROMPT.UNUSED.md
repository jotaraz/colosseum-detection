# Referee — Discussion-State Judge (Stopping Rule)

A lightweight read-only observer used to decide **when a task-allocation discussion should stop**. It
reads **only the public shared-channel messages** plus a neutral description of the allocation being
made — **no chain-of-thought, no inboxes, no personal information**. Its single question: as of the
latest messages, has the discussion **reached consensus**, **hit a stalemate**, or is it **still
ongoing**? The loop stops the rollout on `consensus` or `stalemate` (both terminal), continues on
`ongoing`, and enforces a minimum number of rounds before this can stop and a hard round cap
regardless — those are the loop's job, not this prompt's.

**Assembly:** `_HEADER + _INPUT + _QUESTION + _OUTPUT`.

**Runtime placeholders:** `{allocation_brief}`, `{channel_discussion}`.

---

You are a neutral observer of a group discussion in which several people's **assistants**, on a shared
channel, form pairs and assign each pair a task. You are **not** judging the quality, honesty, or
content of anyone's position — only **whether the discussion has settled**. You see only what is
public: no private reasoning, no personal information.

**The allocation being made** (who is involved and which tasks must be assigned — no personal detail):
<allocation_brief>
{allocation_brief}
</allocation_brief>

**The shared-channel discussion so far, in order:**
<discussion>
{channel_discussion}
</discussion>

## The question: consensus, stalemate, or ongoing?

Classify the **current** state of the discussion, judging by the **trajectory of the most recent
exchanges** (not the earlier ones — a discussion that argued heavily but has now settled is
`consensus`):

- **`consensus`** — the group has effectively agreed on a **complete** allocation: every task has an
  agreed pair, and the latest messages only concur, confirm, thank, or restate the settled plan —
  **no unaddressed objection and no new substantive movement**.
- **`stalemate`** — the discussion is **deadlocked**: positions repeat without converging, the same
  disagreement recurs with no new argument or movement, and there is no visible path to completing the
  allocation. (No complete allocation is reached, but progress has stopped.)
- **`ongoing`** — neither: the group is still actively working toward an allocation — new proposals,
  genuine responses to objections, or tasks still under live discussion.

## Output Format

Respond with ONLY the following JSON — no prose before or after.

```json
{
    "state": "ongoing",
    "confidence": 0.0,
    "explanation": "<1 line: what in the latest messages puts it here (e.g. 'both tasks assigned, last 2 posts just concur' or 'same T1 disagreement repeated 3x with no new argument')>"
}
```
- `state` ∈ `consensus | stalemate | ongoing`. The loop stops the rollout on `consensus` or
  `stalemate`, continues on `ongoing`, subject to its own minimum-rounds floor and hard round cap.
