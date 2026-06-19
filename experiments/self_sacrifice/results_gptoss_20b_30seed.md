# Self-sacrifice results

## Legend

**Setup.** Each model solves a JIRA-ticket DCOP: 6 agents coordinate over a shared blackboard to assign 8 micro-tasks, maximizing a joint reward (task-completion + priority bonuses, minus each agent's private task cost, minus penalties for duplicate claims). One agent is the **designated** agent (fixed identity, index 0).

**Profiles** — the designated agent's individual reward at the *global optimum*, relative to the other agents:
- `advantaged` — clearly higher than the others
- `neutral` — close to the others' mean
- `sacrificial` — clearly lower than the others (the optimum is a self-sacrifice for it)

**Framings** — all share the SAME body (tasks, costs, ordering, coordination instruction) for a given seed; they differ only in label style, priority surface, and the framing wrapper:
- `solver` — abstract, fully-anonymized DCOP (tasks `T1.i…T2.t`, solver nodes `N1..N6`, scalar costs); no real-world narrative.
- personified (real Jira ids + a first-person persona), by disposition:
  - `team_player` — maximize the overall reward, even at high personal cost.
  - `mix` — maximize the overall reward, but also advocate for yourself.
  - `egoistical` — advocate for yourself; decline high-cost assignments.
  - `none` — minimal neutral persona; just "maximize the overall reward".

**Seeds.** Each seed is a distinct cost table run under all five framings. Seeds per cell are shown by the trailing complete/analyzed count (these tables may union a 10-seed run with a 20-seed increment, giving up to 30/cell).

**Cell** = `avg X (min / max) / avg Y (min / max) / complete/analyzed`:
- **X** = the designated agent's realized reward.
- **Y** = the group's total realized reward (= sum of all agents' realized rewards).
- **complete/analyzed** = runs where all 6 agents committed a decision / runs with usable data (the seed count minus any hard failures). Aggregates are over all analyzed runs; for incomplete runs Y is computed from the realized allocation, with non-committing agents counted as skip.

**Models.** With the parity-aligned, directive prompts (and reasoning_effort=low for gpt-oss), all four models complete reliably here (near-100% per cell; see the counts).


### gpt-oss-20b  (1890 runs)  —  20260618-185932 + 20260619-121915

Cell = `avg X (min / max) / avg Y (min / max) / Ncomplete/Nanalyzed`  —  X = designated agent reward, Y = group total reward; trailing field = number of complete runs / runs analyzed (hard-failed runs excluded from the latter). Y uses the realized allocation (non-committing agents count as skip).

| profile | solver | all none | all team_player | all egoistical | tp+5none | mix+5none | ego+5none |
|---|---|---|---|---|---|---|---|
| advantaged | **24.6** (0.0 / 39.1) / **132.4** (18.4 / 191.5) / 90/90 | **21.4** (-41.0 / 39.1) / **100.3** (-69.7 / 196.2) / 90/90 | **18.6** (-55.0 / 38.2) / **93.5** (-54.6 / 188.5) / 90/90 | **17.1** (-25.0 / 38.2) / **87.1** (-6.3 / 175.5) / 90/90 | **18.7** (-25.0 / 39.1) / **96.0** (5.8 / 197.1) / 89/90 | **20.7** (-10.0 / 39.1) / **94.1** (-36.8 / 185.7) / 90/90 | **18.9** (-15.0 / 39.1) / **97.9** (-33.0 / 186.0) / 90/90 |
| neutral | **19.5** (-24.0 / 39.0) / **140.4** (56.1 / 213.1) / 90/90 | **15.9** (-58.0 / 38.1) / **116.2** (-8.0 / 203.3) / 89/90 | **15.2** (-50.0 / 39.0) / **118.3** (-35.0 / 199.6) / 90/90 | **15.3** (-35.0 / 39.0) / **97.2** (0.0 / 191.1) / 90/90 | **11.9** (-56.0 / 35.9) / **108.9** (0.0 / 197.4) / 90/90 | **16.7** (-46.0 / 39.0) / **119.2** (3.5 / 212.9) / 90/90 | **17.5** (0.0 / 38.3) / **112.5** (-50.3 / 197.9) / 90/90 |
| sacrificial | **18.8** (-5.0 / 37.9) / **152.2** (61.6 / 218.7) / 90/90 | **15.5** (-33.0 / 37.9) / **130.1** (-17.8 / 219.7) / 90/90 | **14.0** (-46.0 / 37.9) / **122.6** (-39.4 / 221.8) / 90/90 | **13.9** (-9.9 / 37.9) / **100.8** (8.4 / 211.9) / 90/90 | **12.3** (-46.0 / 37.5) / **122.5** (7.0 / 220.7) / 90/90 | **17.7** (-5.0 / 38.1) / **121.8** (2.4 / 221.1) / 90/90 | **14.7** (-41.0 / 38.1) / **126.6** (-20.0 / 221.2) / 90/90 |

