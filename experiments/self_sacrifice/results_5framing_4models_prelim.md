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

**Seeds.** 10 seeds per profile; each seed is a distinct cost table, run under all five framings (so every cost table appears once per framing).

**Cell** = `avg X (min / max) / avg Y (min / max) / complete/analyzed`:
- **X** = the designated agent's realized reward.
- **Y** = the group's total realized reward (= sum of all agents' realized rewards).
- **complete/analyzed** = runs where all 6 agents committed a decision / runs with usable data (the seed count minus any hard failures). Aggregates are over all analyzed runs; for incomplete runs Y is computed from the realized allocation, with non-committing agents counted as skip.

**Models.** With the parity-aligned, directive prompts (and reasoning_effort=low for gpt-oss), all four models complete reliably here — every cell is 10/10.


### gpt-oss-120b  (450 runs)  —  20260618-132913 + 20260618-123332

Cell = `avg X (min / max) / avg Y (min / max) / Ncomplete/Nanalyzed`  —  X = designated agent reward, Y = group total reward; trailing field = number of complete runs / runs analyzed (hard-failed runs excluded from the latter). Y uses the realized allocation (non-committing agents count as skip).

| profile | solver | team_player | mix | egoistical | none |
|---|---|---|---|---|---|
| advantaged | **32.5** (0.0 / 39.1) / **151.4** (107.0 / 198.5) / 30/30 | **32.3** (16.4 / 39.1) / **145.2** (93.8 / 197.0) / 30/30 | **30.7** (18.5 / 38.2) / **142.6** (84.4 / 196.2) / 30/30 | **31.2** (18.5 / 39.1) / **143.4** (100.9 / 191.5) / 30/30 | **32.8** (22.4 / 39.0) / **141.5** (94.1 / 187.7) / 30/30 |
| neutral | **28.0** (0.0 / 39.0) / **153.0** (85.8 / 208.0) / 30/30 | **27.3** (17.8 / 38.1) / **148.8** (43.2 / 210.8) / 30/30 | **24.3** (-46.0 / 39.0) / **139.2** (65.3 / 213.1) / 30/30 | **26.2** (0.0 / 38.1) / **134.3** (69.6 / 205.2) / 30/30 | **27.4** (16.2 / 38.1) / **147.0** (80.3 / 204.3) / 30/30 |
| sacrificial | **25.9** (8.4 / 38.1) / **168.3** (117.9 / 221.6) / 30/30 | **21.6** (-21.0 / 37.9) / **167.0** (104.8 / 220.2) / 30/30 | **23.4** (10.0 / 38.1) / **159.7** (72.1 / 220.4) / 30/30 | **23.6** (-5.0 / 38.1) / **157.3** (90.5 / 223.7) / 30/30 | **22.9** (-16.0 / 37.9) / **163.2** (37.6 / 223.6) / 30/30 |

### gpt-oss-20b  (450 runs)  —  20260618-133942 + 20260618-123332

Cell = `avg X (min / max) / avg Y (min / max) / Ncomplete/Nanalyzed`  —  X = designated agent reward, Y = group total reward; trailing field = number of complete runs / runs analyzed (hard-failed runs excluded from the latter). Y uses the realized allocation (non-committing agents count as skip).

| profile | solver | team_player | mix | egoistical | none |
|---|---|---|---|---|---|
| advantaged | **26.2** (0.0 / 39.1) / **128.7** (49.0 / 186.7) / 30/30 | **28.1** (0.0 / 39.0) / **97.5** (-45.4 / 197.4) / 28/30 | **30.8** (0.0 / 39.1) / **111.5** (0.8 / 184.3) / 30/30 | **18.8** (-55.0 / 39.1) / **88.3** (10.0 / 178.7) / 30/30 | **28.1** (-10.0 / 39.1) / **100.4** (0.0 / 176.9) / 30/30 |
| neutral | **22.6** (0.0 / 38.1) / **130.3** (81.9 / 211.6) / 30/30 | **23.7** (0.0 / 39.0) / **110.4** (-16.9 / 190.3) / 30/30 | **24.5** (0.0 / 38.1) / **111.3** (19.0 / 203.6) / 30/30 | **22.2** (0.0 / 38.3) / **94.9** (43.8 / 167.2) / 30/30 | **21.4** (0.0 / 36.7) / **115.4** (49.6 / 190.8) / 30/30 |
| sacrificial | **21.6** (0.0 / 37.9) / **151.0** (56.9 / 206.4) / 30/30 | **16.9** (-32.0 / 37.5) / **112.8** (-31.4 / 210.5) / 30/30 | **15.8** (-32.0 / 38.1) / **119.5** (61.5 / 207.7) / 30/30 | **19.3** (-15.0 / 37.9) / **89.9** (20.1 / 183.7) / 30/30 | **19.3** (-32.0 / 37.9) / **125.4** (47.5 / 181.9) / 30/30 |

### Llama-3.3-70B-Instruct  (450 runs)  —  20260618-132918 + 20260618-123338

Cell = `avg X (min / max) / avg Y (min / max) / Ncomplete/Nanalyzed`  —  X = designated agent reward, Y = group total reward; trailing field = number of complete runs / runs analyzed (hard-failed runs excluded from the latter). Y uses the realized allocation (non-committing agents count as skip).

| profile | solver | team_player | mix | egoistical | none |
|---|---|---|---|---|---|
| advantaged | **30.9** (18.1 / 39.1) / **139.3** (62.4 / 188.1) / 30/30 | **32.9** (21.7 / 39.0) / **140.9** (42.7 / 192.2) / 30/30 | **32.2** (20.9 / 39.1) / **147.0** (104.9 / 196.4) / 30/30 | **31.9** (0.0 / 39.1) / **125.4** (61.8 / 165.4) / 30/30 | **32.8** (21.4 / 39.1) / **143.5** (105.7 / 188.9) / 30/30 |
| neutral | **28.1** (20.1 / 39.0) / **149.1** (34.2 / 205.4) / 30/30 | **28.1** (20.0 / 38.1) / **143.0** (5.7 / 211.0) / 30/30 | **27.9** (17.8 / 38.3) / **143.4** (68.9 / 213.5) / 30/30 | **28.5** (20.1 / 38.3) / **116.6** (69.1 / 167.9) / 30/30 | **28.5** (19.2 / 38.7) / **143.4** (100.4 / 204.0) / 30/30 |
| sacrificial | **25.2** (11.0 / 38.1) / **165.1** (67.2 / 221.7) / 30/30 | **24.9** (10.0 / 37.9) / **162.6** (104.2 / 202.8) / 30/30 | **23.6** (8.4 / 38.1) / **164.6** (104.9 / 223.8) / 30/30 | **24.9** (0.0 / 38.1) / **126.4** (74.8 / 187.4) / 30/30 | **23.5** (3.0 / 38.1) / **161.6** (106.8 / 221.6) / 30/30 |

### Qwen2.5-72B-Instruct  (382 runs)  —  20260618-132917 + 20260618-123338

Cell = `avg X (min / max) / avg Y (min / max) / Ncomplete/Nanalyzed`  —  X = designated agent reward, Y = group total reward; trailing field = number of complete runs / runs analyzed (hard-failed runs excluded from the latter). Y uses the realized allocation (non-committing agents count as skip).

| profile | solver | team_player | mix | egoistical | none |
|---|---|---|---|---|---|
| advantaged | **31.2** (11.4 / 39.1) / **138.8** (52.6 / 192.5) / 30/30 | **30.3** (8.8 / 39.0) / **120.9** (37.7 / 192.5) / 30/30 | **30.0** (10.9 / 39.1) / **101.2** (22.4 / 174.2) / 30/30 | **29.4** (10.9 / 39.1) / **97.7** (33.8 / 157.4) / 30/30 | **30.1** (0.0 / 39.1) / **114.5** (28.4 / 174.2) / 30/30 |
| neutral | **26.2** (0.0 / 37.7) / **143.0** (56.7 / 205.1) / 30/30 | **24.3** (-20.0 / 38.1) / **127.2** (58.6 / 213.0) / 30/30 | **25.2** (0.0 / 39.0) / **110.4** (22.0 / 157.0) / 30/30 | **25.2** (12.6 / 39.0) / **105.5** (37.7 / 170.6) / 30/30 | **23.9** (-1.0 / 37.7) / **114.5** (31.7 / 194.7) / 30/30 |
| sacrificial | **25.6** (7.3 / 37.9) / **167.3** (79.0 / 219.9) / 17/17 | **23.1** (10.0 / 37.9) / **156.6** (97.3 / 197.6) / 16/16 | **22.6** (0.0 / 37.5) / **133.8** (0.0 / 197.8) / 17/17 | **25.6** (8.8 / 37.9) / **134.2** (50.9 / 185.8) / 16/16 | **21.1** (-5.0 / 37.9) / **125.6** (61.5 / 182.7) / 16/16 |

