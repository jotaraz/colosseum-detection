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


### gpt-oss-120b  (210 runs)  —  20260618-161935 + 20260618-123332

Cell = `avg X (min / max) / avg Y (min / max) / Ncomplete/Nanalyzed`  —  X = designated agent reward, Y = group total reward; trailing field = number of complete runs / runs analyzed (hard-failed runs excluded from the latter). Y uses the realized allocation (non-committing agents count as skip).

| profile | solver | all none | team_player + 5×none | egoistical + 5×none |
|---|---|---|---|---|
| advantaged | **31.7** (21.4 / 37.8) / **149.1** (128.4 / 190.8) / 10/10 | **31.9** (24.8 / 36.4) / **130.9** (94.1 / 164.7) / 10/10 | **29.7** (18.5 / 37.8) / **128.6** (60.4 / 190.4) / 10/10 | **31.9** (21.7 / 37.8) / **140.6** (108.5 / 192.5) / 10/10 |
| neutral | **25.4** (0.0 / 34.8) / **153.0** (93.7 / 193.9) / 10/10 | **27.1** (19.2 / 34.8) / **156.8** (106.9 / 199.8) / 10/10 | **21.9** (-10.0 / 33.3) / **151.9** (94.2 / 199.1) / 10/10 | **27.5** (22.1 / 34.8) / **149.2** (108.2 / 197.5) / 10/10 |
| sacrificial | **28.1** (12.0 / 37.9) / **179.2** (147.4 / 221.6) / 10/10 | **30.5** (22.0 / 37.9) / **180.9** (122.7 / 220.2) / 10/10 | **23.2** (5.0 / 37.9) / **179.2** (137.5 / 217.3) / 10/10 | **26.6** (12.6 / 37.9) / **186.1** (164.2 / 218.7) / 10/10 |

### gpt-oss-20b  (210 runs)  —  20260618-161936 + 20260618-123332

Cell = `avg X (min / max) / avg Y (min / max) / Ncomplete/Nanalyzed`  —  X = designated agent reward, Y = group total reward; trailing field = number of complete runs / runs analyzed (hard-failed runs excluded from the latter). Y uses the realized allocation (non-committing agents count as skip).

| profile | solver | all none | team_player + 5×none | egoistical + 5×none |
|---|---|---|---|---|
| advantaged | **30.3** (22.8 / 35.7) / **129.8** (49.0 / 166.7) / 10/10 | **24.8** (-10.0 / 37.8) / **71.8** (0.0 / 130.5) / 10/10 | **31.1** (16.7 / 37.8) / **99.0** (52.1 / 153.6) / 10/10 | **26.8** (18.5 / 36.4) / **78.0** (-17.7 / 190.2) / 10/10 |
| neutral | **17.6** (0.0 / 33.3) / **131.9** (83.0 / 203.2) / 10/10 | **21.4** (0.0 / 33.5) / **118.2** (54.0 / 164.0) / 10/10 | **17.6** (-20.0 / 34.8) / **120.6** (53.8 / 196.1) / 10/10 | **24.8** (0.0 / 34.8) / **123.9** (68.3 / 191.8) / 10/10 |
| sacrificial | **28.0** (19.6 / 37.9) / **164.3** (98.1 / 198.6) / 10/10 | **29.4** (16.0 / 37.9) / **137.5** (95.2 / 181.9) / 10/10 | **21.1** (0.0 / 37.5) / **137.2** (102.6 / 189.3) / 10/10 | **19.0** (0.0 / 37.9) / **134.5** (66.2 / 192.9) / 10/10 |

### Llama-3.3-70B-Instruct  (210 runs)  —  20260618-161937 + 20260618-123338

Cell = `avg X (min / max) / avg Y (min / max) / Ncomplete/Nanalyzed`  —  X = designated agent reward, Y = group total reward; trailing field = number of complete runs / runs analyzed (hard-failed runs excluded from the latter). Y uses the realized allocation (non-committing agents count as skip).

| profile | solver | all none | team_player + 5×none | egoistical + 5×none |
|---|---|---|---|---|
| advantaged | **30.5** (18.5 / 37.8) / **135.0** (97.5 / 169.1) / 10/10 | **32.0** (21.7 / 37.8) / **134.7** (105.7 / 152.9) / 10/10 | **32.8** (26.6 / 37.8) / **145.7** (105.5 / 192.4) / 10/10 | **29.6** (21.7 / 35.7) / **128.1** (105.9 / 190.6) / 10/10 |
| neutral | **26.7** (22.1 / 34.8) / **156.1** (97.9 / 195.0) / 10/10 | **27.8** (19.2 / 34.8) / **144.3** (105.6 / 193.7) / 10/10 | **28.0** (22.1 / 33.5) / **151.8** (69.5 / 206.0) / 10/10 | **27.9** (22.1 / 34.8) / **152.9** (92.2 / 201.5) / 10/10 |
| sacrificial | **27.2** (12.0 / 37.9) / **182.3** (149.7 / 221.7) / 10/10 | **28.1** (18.8 / 36.9) / **180.0** (151.5 / 219.7) / 10/10 | **28.8** (21.1 / 37.1) / **174.2** (129.0 / 219.8) / 10/10 | **26.3** (18.8 / 37.9) / **175.5** (148.3 / 219.9) / 10/10 |

### Qwen2.5-72B-Instruct  (210 runs)  —  20260618-161935 + 20260618-123338

Cell = `avg X (min / max) / avg Y (min / max) / Ncomplete/Nanalyzed`  —  X = designated agent reward, Y = group total reward; trailing field = number of complete runs / runs analyzed (hard-failed runs excluded from the latter). Y uses the realized allocation (non-committing agents count as skip).

| profile | solver | all none | team_player + 5×none | egoistical + 5×none |
|---|---|---|---|---|
| advantaged | **30.5** (11.4 / 37.8) / **133.1** (75.3 / 192.5) / 10/10 | **29.9** (0.0 / 37.8) / **108.3** (39.2 / 165.2) / 10/10 | **29.7** (16.6 / 37.2) / **114.6** (75.2 / 145.3) / 10/10 | **28.0** (6.9 / 37.8) / **100.0** (40.4 / 141.2) / 10/10 |
| neutral | **27.4** (22.1 / 33.5) / **154.2** (65.8 / 198.7) / 10/10 | **25.3** (-1.0 / 33.5) / **119.2** (31.7 / 194.7) / 10/10 | **28.1** (22.1 / 33.5) / **122.5** (79.0 / 161.9) / 10/10 | **23.1** (12.6 / 33.3) / **117.7** (70.9 / 165.8) / 10/10 |
| sacrificial | **27.8** (7.3 / 37.9) / **177.0** (110.3 / 219.9) / 10/10 | **24.2** (8.8 / 37.9) / **136.4** (90.6 / 182.7) / 10/10 | **26.3** (10.0 / 37.5) / **131.0** (53.0 / 180.2) / 10/10 | **25.7** (11.5 / 37.9) / **136.3** (7.1 / 183.8) / 10/10 |

