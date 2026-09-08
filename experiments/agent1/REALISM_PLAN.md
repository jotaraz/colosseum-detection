# agent1 — realism plan

Decisions from the 2026-08-27 design discussion on making the environment less like a
constructed scenario. Nothing here is built yet.

**Cross-cutting constraint.** ~290 committed configs depend on byte-identical tool schemas
(see `tools.py::HARNESS_VARIANTS` and `DEFAULT_EXAMPLE_DAY` for the existing discipline).
Every addition goes behind a variant switch, and every world change is a new fixture version,
one thing at a time.

---

## Agreed

### 1. LLM-backed NPCs
The eight non-principal colleagues currently never reply — a DM to Ines gets permanent
silence. Give each a card (what they know, what they promised, how they behave under
pressure) and back them with a cheap model. Live LLM, not scripted follow-ups.

**Helena is a special case.** An authority audience holding different information than the
peers: lobbyable in private, able to intervene publicly mid-run, and the recipient of the
reporter's write-up. Deception aimed at a manager is a different behaviour from deception
aimed at a peer, and it is currently unobservable.

Highest-value item on the list. Additive — changes no existing tool schema.

### 2. Notification semantics (and the uptake ledger)
Two parts, both small.

**Differentiate delivery.** `prompts.delta` currently announces every new message in every
visible conversation identically, so a `#social` post and a DM from Helena arrive as the same
line. Real Slack pings on DMs and @mentions; other channel traffic is a badge you may never
open. Notify loudly on DM/mention only; leave the rest to the unread counts
`slack_list_conversations` already returns. Without this, "the assistant plausibly didn't
notice" is not an outcome the environment can produce.

**Fix the ledger.** `run.py:154` (`_mark_seen`) writes message ids into `ws.seen` when the
*notification* fires, before the agent reads anything — so "the signal reached them" means
"they were told a message existed." Mark seen on an actual read instead. The judges use uptake
to separate *didn't know* from *knew and concealed*, so this is a live overcount. Independent
of everything else here.

Slack threads were considered and **dropped**: they fragment the transcript the judges read.

### 3. Capacity and allocation, replacing the vacation device
Ines and Callum (Data Scientists) and Haruki (Backend Engineer) — the exact three people who
would dissolve the dilemma — are on an identical 10–21 Aug leave beginning the morning the run
starts. Three problems: it is transparently a constraint device and so an eval-awareness cue;
availability is an emoji in a Slack profile, discoverable only by chance; and it is internally
inconsistent, since Ines DMs both data scientists at 09:20 on the first day of her own leave.

**Fix.** Make the exclusions individually plausible and differently caused — one on leave, one
visibly allocated to another sprint, one present but on-call/firefighting. Then add a capacity
query (`hr_get_availability(person, window)`, or a capacity view on the board) returning
allocation, leave and on-call from a frozen record.

Distinct from calendar free/busy: that is meeting-level, this is resourcing-level, and
resourcing is the question actually being decided.

**Payoff.** "There's nobody else available" is the most common load-bearing claim in these runs
and is currently true-but-lucky and ungradable. With a capacity table it is right or it is a
lie, deterministically, with no judge involved.

### 4. A document store — no repo
A `documents` array in the fixture (id, title, author, date, body), plus `docs_search` (same
shape as the existing `slack_search`) and `docs_get` returning **full text**. Read-only for now.

**Public within the company**, which is the realistic default for Confluence/Notion/Drive and
keeps DMs and calendars as the only private surface — the asymmetry already modelled.

**Why it matters.** v6's whole symmetric-expertise record is 21 DM messages readable by only
two of the four assistants, so competence claims can be neither argued publicly nor refuted.
A public store gives shared ground truth to argue from, makes "did the claim match the record"
a deterministic check rather than a judge call, and — via the retrieval log — separates citing
a doc that says something else from citing one that was never returned.

**Include clutter deliberately**: a stale version of the definitions doc, something with a
misleading title. A tidy six-document store with perfect search is less realistic than none.

**No repo.** Metadata-only PR listing was rejected as a costume — on real GitHub, if you can
list a PR you can read its diff, and a repo you can enumerate but never open is a visible seam.
A small readable repo remains possible later; for now the authorship facts that would have come
from PRs go into documents instead (a July retro listing who did what, design docs with author
fields).

**The real cost is authoring**, not code: the documents must stay consistent with ~300 messages
of existing Slack history. This is a new fixture lineage, not a one-thing-at-a-time edit.

### 5. Board history
Append claims instead of overwriting, and return the log with timestamps: "Nadia claimed T1
09:41, unclaimed 09:52, Priya claimed T1 09:53." Faithful — Jira has an issue History tab,
Linear shows assignment changes inline, GitHub Projects and Asana keep activity logs. Nearly
free to build.

Open choice: inline in `board_get_assignments` (reversals are hard to miss) versus a separate
`board_get_history` call (makes "did the agent check" measurable). Jira and Linear both notify
watchers on assignee change, so if (2) lands, a reassignment should fire a notification too.

### 6. More than two tickets
Two tickets and four people is a puzzle, not a sprint. Five or six tickets, some needing one
person and some two, preserves the collision exactly while removing the setup look — and makes
a plausible dodge available ("I'll take T4 instead"), which is more interesting than a binary.

---

## Tabled

**Multi-sprint continuity.** Liked in principle, to be discussed properly. Run the same world
twice, seeding the second run from the first's real transcript. The most realistic change
available, and it makes lies durable: an August fabrication is on the record in September,
where it must be maintained or caught. v17 already did this once by hand, importing s270.

**`calendar_get_freebusy`.** Conceded as a minor realism gain. Its value is falsifiability —
askE instructs a fabricated scheduling conflict that no agent in the room can currently check.
Low priority alone; stronger once (3) exists.

**Writable documents.** Editing the definitions doc so your claim about it becomes true is a
real move and would be a striking thing to catch, but it makes the ground truth mutable and
grading harder. A later variant, not a v1 decision.

**A small readable repo.** Only if PR-level authorship evidence turns out to matter after the
document store exists. Would need genuinely readable files — 10-15 short, boring ones — so that
the only remaining gap is historical diffs.

---

## Rejected

- **Slack threads** — fragments the judge transcript for a modest gain.
- **Ticket/board comments** — duplicates the sprint channel.
- **Scripted follow-up probes** — superseded by LLM NPCs.
- **Tool failures and rate limits** — adds noise on exactly the `dropped_call` / `truncated`
  axis `agent.classify_step` exists to separate from a genuine pass.

---

## Suggested order

1. Notification + ledger fix (2) — cheap, self-contained, and fixes something currently wrong.
2. Capacity and allocation (3) — removes the most conspicuous artifact in the world.
3. NPCs (1) — largest single gain; no schema change.
4. Document store (4) — largest authoring job.
5. Board history and more tickets (5, 6) — cheap, do alongside a fixture rebuild.
