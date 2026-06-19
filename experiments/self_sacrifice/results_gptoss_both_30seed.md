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
- **X** = the designated agent's realized task cost (private cost of the task it committed; a skip / non-committing agent counts as cost 0).
- **Y** = the group's total realized reward (= sum of all agents' realized rewards).
- **complete/analyzed** = runs where all 6 agents committed a decision / runs with usable data (the seed count minus any hard failures). Aggregates are over all analyzed runs; for incomplete runs Y is computed from the realized allocation, with non-committing agents counted as skip.

**Models.** With the parity-aligned, directive prompts (and reasoning_effort=low for gpt-oss), all four models complete reliably here (near-100% per cell; see the counts).


### gpt-oss-120b  (1890 runs)  —  20260618-185931 + 20260619-121915

Cell = `avg X (min / max) / avg Y (min / max) / Ncomplete/Nanalyzed`  —  X = designated agent task cost, Y = group total reward; trailing field = number of complete runs / runs analyzed (hard-failed runs excluded from the latter). Y uses the realized allocation (non-committing agents count as skip).

| profile | solver | all none | all team_player | all egoistical | tp+5none | mix+5none | ego+5none |
|---|---|---|---|---|---|---|---|
| advantaged | **3.4** (0.9 / 12.1) / **154.0** (97.0 / 201.4) / 90/90 | **3.2** (0.9 / 6.9) / **146.6** (52.3 / 198.6) / 90/90 | **3.8** (1.3 / 8.6) / **148.0** (35.8 / 198.9) / 90/90 | **3.7** (0.9 / 13.2) / **147.1** (71.7 / 196.0) / 90/90 | **4.1** (1.4 / 12.0) / **149.5** (60.5 / 198.9) / 90/90 | **3.5** (0.9 / 9.1) / **148.5** (98.2 / 198.6) / 90/90 | **3.9** (0.9 / 40.0) / **148.0** (90.7 / 198.8) / 90/90 |
| neutral | **3.5** (1.0 / 10.0) / **156.1** (81.7 / 213.1) / 90/90 | **4.2** (1.0 / 28.0) / **144.9** (77.0 / 213.9) / 90/90 | **4.5** (1.2 / 28.0) / **151.6** (35.6 / 211.0) / 90/90 | **4.8** (1.0 / 40.0) / **145.1** (46.4 / 214.0) / 90/90 | **5.3** (1.2 / 40.0) / **148.7** (67.2 / 211.7) / 90/90 | **3.6** (1.0 / 15.6) / **147.4** (65.8 / 213.9) / 90/90 | **3.7** (1.0 / 13.3) / **148.0** (93.5 / 213.9) / 90/90 |
| sacrificial | **7.2** (1.1 / 40.0) / **171.9** (116.4 / 223.5) / 90/90 | **5.6** (1.1 / 16.6) / **166.6** (90.1 / 223.7) / 90/90 | **8.3** (1.5 / 42.0) / **166.4** (79.3 / 223.6) / 90/90 | **5.5** (1.1 / 16.6) / **165.3** (78.4 / 223.5) / 90/90 | **8.8** (1.1 / 56.0) / **164.3** (96.4 / 223.5) / 90/90 | **5.3** (1.1 / 40.0) / **163.5** (73.1 / 223.5) / 90/90 | **4.8** (1.1 / 16.6) / **164.3** (58.3 / 223.4) / 90/90 |

### gpt-oss-20b  (1890 runs)  —  20260618-185932 + 20260619-121915

Cell = `avg X (min / max) / avg Y (min / max) / Ncomplete/Nanalyzed`  —  X = designated agent task cost, Y = group total reward; trailing field = number of complete runs / runs analyzed (hard-failed runs excluded from the latter). Y uses the realized allocation (non-committing agents count as skip).

| profile | solver | all none | all team_player | all egoistical | tp+5none | mix+5none | ego+5none |
|---|---|---|---|---|---|---|---|
| advantaged | **4.8** (0.9 / 28.0) / **132.4** (18.4 / 191.5) / 90/90 | **9.0** (0.9 / 56.0) / **100.3** (-69.7 / 196.2) / 90/90 | **8.9** (1.6 / 80.0) / **93.5** (-54.6 / 188.5) / 90/90 | **5.1** (1.6 / 50.0) / **87.1** (-6.3 / 175.5) / 90/90 | **7.4** (0.9 / 50.0) / **96.0** (5.8 / 197.1) / 89/90 | **5.0** (0.9 / 40.0) / **94.1** (-36.8 / 185.7) / 90/90 | **4.6** (0.9 / 40.0) / **97.9** (-33.0 / 186.0) / 90/90 |
| neutral | **5.7** (1.0 / 49.0) / **140.4** (56.1 / 213.1) / 90/90 | **8.7** (1.1 / 83.0) / **116.2** (-8.0 / 203.3) / 89/90 | **11.0** (1.0 / 80.0) / **118.3** (-35.0 / 199.6) / 90/90 | **6.0** (1.0 / 70.0) / **97.2** (0.0 / 191.1) / 90/90 | **16.5** (1.2 / 71.0) / **108.9** (0.0 / 197.4) / 90/90 | **6.2** (1.0 / 71.0) / **119.2** (3.5 / 212.9) / 90/90 | **4.5** (1.2 / 35.0) / **112.5** (-50.3 / 197.9) / 90/90 |
| sacrificial | **9.4** (1.1 / 40.0) / **152.2** (61.6 / 218.7) / 90/90 | **11.2** (1.1 / 73.0) / **130.1** (-17.8 / 219.7) / 90/90 | **14.3** (1.1 / 81.0) / **122.6** (-39.4 / 221.8) / 90/90 | **9.4** (1.3 / 49.9) / **100.8** (8.4 / 211.9) / 90/90 | **17.4** (1.1 / 81.0) / **122.5** (7.0 / 220.7) / 90/90 | **9.1** (1.1 / 40.0) / **121.8** (2.4 / 221.1) / 90/90 | **11.4** (1.1 / 81.0) / **126.6** (-20.0 / 221.2) / 90/90 |

