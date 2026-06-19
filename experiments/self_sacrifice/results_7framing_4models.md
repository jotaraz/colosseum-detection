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


### gpt-oss-120b  (630 runs)  —  20260618-185931

Cell = `avg X (min / max) / avg Y (min / max) / Ncomplete/Nanalyzed`  —  X = designated agent reward, Y = group total reward; trailing field = number of complete runs / runs analyzed (hard-failed runs excluded from the latter). Y uses the realized allocation (non-committing agents count as skip).

| profile | solver | all none | all team_player | all egoistical | tp+5none | mix+5none | ego+5none |
|---|---|---|---|---|---|---|---|
| advantaged | **30.2** (18.5 / 37.8) / **145.9** (103.2 / 171.6) / 30/30 | **31.9** (20.9 / 37.8) / **138.9** (52.3 / 192.5) / 30/30 | **31.2** (0.0 / 37.8) / **136.3** (35.8 / 190.7) / 30/30 | **26.3** (0.0 / 37.8) / **139.4** (71.7 / 192.4) / 30/30 | **31.5** (16.9 / 37.8) / **147.3** (105.0 / 193.1) / 30/30 | **25.4** (0.0 / 37.8) / **139.2** (99.8 / 191.5) / 30/30 | **26.6** (0.0 / 37.8) / **133.7** (90.7 / 191.5) / 30/30 |
| neutral | **27.0** (19.2 / 34.8) / **159.1** (95.5 / 206.3) / 30/30 | **25.5** (-3.0 / 34.8) / **145.9** (77.0 / 203.5) / 30/30 | **24.8** (0.0 / 34.8) / **153.9** (84.6 / 203.6) / 30/30 | **23.5** (0.0 / 33.3) / **148.2** (91.3 / 199.5) / 30/30 | **23.1** (0.0 / 33.5) / **151.8** (81.0 / 206.0) / 30/30 | **25.4** (0.0 / 33.5) / **147.2** (77.0 / 200.6) / 30/30 | **26.1** (0.0 / 34.8) / **152.3** (101.4 / 200.9) / 30/30 |
| sacrificial | **29.8** (21.5 / 37.9) / **192.3** (137.6 / 221.6) / 30/30 | **26.3** (0.0 / 37.9) / **182.4** (128.7 / 221.7) / 30/30 | **25.3** (0.0 / 37.9) / **183.5** (145.6 / 220.4) / 30/30 | **24.7** (0.0 / 37.9) / **184.5** (133.7 / 220.9) / 30/30 | **27.4** (10.0 / 37.9) / **179.7** (130.9 / 221.7) / 30/30 | **26.0** (18.8 / 37.9) / **181.0** (134.1 / 217.1) / 30/30 | **26.1** (12.6 / 37.9) / **178.9** (129.8 / 221.8) / 30/30 |

### gpt-oss-20b  (630 runs)  —  20260618-185932

Cell = `avg X (min / max) / avg Y (min / max) / Ncomplete/Nanalyzed`  —  X = designated agent reward, Y = group total reward; trailing field = number of complete runs / runs analyzed (hard-failed runs excluded from the latter). Y uses the realized allocation (non-committing agents count as skip).

| profile | solver | all none | all team_player | all egoistical | tp+5none | mix+5none | ego+5none |
|---|---|---|---|---|---|---|---|
| advantaged | **23.4** (0.0 / 37.8) / **128.2** (40.4 / 191.5) / 30/30 | **23.0** (-41.0 / 37.2) / **88.7** (-69.7 / 162.5) / 30/30 | **23.1** (-10.0 / 37.8) / **86.2** (-19.4 / 162.5) / 30/30 | **17.3** (0.0 / 37.8) / **79.7** (-6.3 / 165.6) / 30/30 | **17.6** (0.0 / 37.8) / **88.1** (5.8 / 150.4) / 30/30 | **19.7** (-10.0 / 37.8) / **81.3** (-20.1 / 154.7) / 30/30 | **18.1** (0.0 / 37.8) / **90.6** (-6.9 / 154.7) / 30/30 |
| neutral | **19.2** (-24.0 / 34.8) / **140.5** (56.1 / 199.6) / 30/30 | **18.6** (-24.0 / 34.8) / **120.3** (46.1 / 203.3) / 29/30 | **12.6** (-50.0 / 30.7) / **121.3** (24.7 / 199.6) / 30/30 | **14.0** (0.0 / 34.8) / **106.3** (35.1 / 191.1) / 30/30 | **9.7** (-56.0 / 33.5) / **111.7** (18.6 / 197.4) / 30/30 | **10.4** (-46.0 / 28.5) / **117.8** (51.6 / 164.4) / 30/30 | **17.1** (0.0 / 34.8) / **117.5** (27.0 / 197.9) / 30/30 |
| sacrificial | **23.8** (0.0 / 37.9) / **173.4** (102.1 / 218.7) / 30/30 | **18.7** (-33.0 / 37.9) / **148.7** (73.0 / 213.5) / 30/30 | **19.0** (0.0 / 37.9) / **143.8** (-2.7 / 221.8) / 30/30 | **14.8** (-9.9 / 37.9) / **109.4** (8.4 / 211.9) / 30/30 | **15.1** (-33.0 / 37.5) / **145.3** (58.6 / 220.3) / 30/30 | **19.5** (0.0 / 37.9) / **138.0** (57.3 / 221.1) / 30/30 | **17.3** (-20.0 / 37.9) / **148.1** (72.6 / 221.2) / 30/30 |

### Llama-3.3-70B-Instruct  (630 runs)  —  20260618-185938

Cell = `avg X (min / max) / avg Y (min / max) / Ncomplete/Nanalyzed`  —  X = designated agent reward, Y = group total reward; trailing field = number of complete runs / runs analyzed (hard-failed runs excluded from the latter). Y uses the realized allocation (non-committing agents count as skip).

| profile | solver | all none | all team_player | all egoistical | tp+5none | mix+5none | ego+5none |
|---|---|---|---|---|---|---|---|
| advantaged | **29.4** (10.0 / 37.8) / **137.4** (87.5 / 170.5) / 30/30 | **31.6** (20.9 / 37.2) / **139.4** (90.2 / 190.2) / 30/30 | **31.1** (21.4 / 37.8) / **129.0** (62.2 / 190.7) / 30/30 | **31.1** (21.7 / 35.7) / **146.5** (93.1 / 190.0) / 30/30 | **30.0** (-10.0 / 37.2) / **137.1** (89.6 / 190.7) / 30/30 | **30.8** (20.9 / 36.4) / **140.1** (59.6 / 192.6) / 30/30 | **30.3** (20.9 / 35.7) / **134.6** (48.9 / 192.6) / 30/30 |
| neutral | **26.4** (-10.0 / 34.8) / **156.5** (75.7 / 206.0) / 30/30 | **27.5** (19.2 / 33.5) / **149.7** (85.6 / 205.9) / 30/30 | **26.8** (9.0 / 33.5) / **155.1** (69.2 / 205.0) / 30/30 | **26.6** (21.4 / 33.5) / **156.3** (95.8 / 194.3) / 30/30 | **27.3** (19.2 / 33.5) / **144.9** (52.7 / 205.9) / 30/30 | **27.2** (19.9 / 33.5) / **147.8** (78.9 / 205.9) / 30/30 | **27.5** (22.1 / 33.5) / **143.4** (45.5 / 201.5) / 30/30 |
| sacrificial | **26.2** (17.3 / 37.9) / **184.4** (144.0 / 220.0) / 30/30 | **28.6** (18.8 / 37.9) / **172.0** (127.5 / 220.0) / 30/30 | **20.0** (-33.0 / 37.9) / **167.7** (106.8 / 217.1) / 30/30 | **26.2** (18.8 / 37.9) / **185.6** (145.1 / 219.8) / 30/30 | **19.4** (-33.0 / 37.9) / **164.3** (87.9 / 219.8) / 30/30 | **27.2** (18.8 / 37.9) / **168.4** (94.0 / 217.2) / 30/30 | **26.7** (18.8 / 37.9) / **177.0** (111.4 / 211.4) / 30/30 |

### Qwen2.5-72B-Instruct  (630 runs)  —  20260618-185937

Cell = `avg X (min / max) / avg Y (min / max) / Ncomplete/Nanalyzed`  —  X = designated agent reward, Y = group total reward; trailing field = number of complete runs / runs analyzed (hard-failed runs excluded from the latter). Y uses the realized allocation (non-committing agents count as skip).

| profile | solver | all none | all team_player | all egoistical | tp+5none | mix+5none | ego+5none |
|---|---|---|---|---|---|---|---|
| advantaged | **30.2** (5.9 / 37.8) / **128.5** (41.0 / 192.4) / 30/30 | **28.8** (3.5 / 37.8) / **112.2** (53.8 / 164.7) / 30/30 | **29.0** (5.7 / 37.8) / **115.0** (45.6 / 192.5) / 30/30 | **26.2** (0.0 / 37.8) / **91.6** (29.8 / 153.4) / 30/30 | **31.1** (13.1 / 37.8) / **113.1** (39.2 / 165.2) / 30/30 | **28.0** (0.0 / 37.8) / **95.4** (22.8 / 144.8) / 30/30 | **27.7** (0.0 / 37.8) / **94.9** (22.8 / 152.2) / 30/30 |
| neutral | **25.2** (0.0 / 33.5) / **147.9** (86.1 / 200.1) / 30/30 | **25.2** (-13.0 / 34.8) / **126.3** (66.0 / 205.3) / 30/30 | **20.5** (-46.0 / 34.8) / **145.2** (65.2 / 196.6) / 30/30 | **22.7** (0.0 / 33.3) / **113.7** (29.9 / 177.7) / 30/30 | **24.4** (0.0 / 34.8) / **125.5** (65.8 / 176.0) / 30/30 | **24.9** (12.1 / 33.3) / **121.0** (55.8 / 180.1) / 30/30 | **26.1** (12.1 / 34.8) / **118.9** (46.7 / 163.3) / 30/30 |
| sacrificial | **27.1** (8.8 / 37.9) / **180.6** (132.7 / 221.5) / 30/30 | **22.7** (-20.0 / 37.9) / **142.9** (78.8 / 184.1) / 30/30 | **18.6** (-5.0 / 37.5) / **163.1** (42.4 / 220.4) / 30/30 | **23.8** (0.0 / 37.9) / **134.4** (-2.2 / 183.8) / 30/30 | **21.4** (-20.0 / 37.5) / **149.8** (89.1 / 192.7) / 30/30 | **25.1** (0.0 / 37.9) / **148.3** (65.1 / 193.2) / 30/30 | **23.4** (0.0 / 37.5) / **154.3** (33.6 / 216.2) / 30/30 |

