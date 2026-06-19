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


### gpt-oss-120b  (150 runs)  —  experiments/self_sacrifice/outputs/self_sacrifice_n6_gptoss_120b/20260618-123332

Cell = `avg X (min / max) / avg Y (min / max) / Ncomplete/Nanalyzed`  —  X = designated agent reward, Y = group total reward; trailing field = number of complete runs / runs analyzed (hard-failed runs excluded from the latter). Y uses the realized allocation (non-committing agents count as skip).

| profile | solver | team_player | mix | egoistical | none |
|---|---|---|---|---|---|
| advantaged | **31.7** (21.4 / 37.8) / **149.1** (128.4 / 190.8) / 10/10 | **31.4** (21.4 / 37.2) / **145.9** (97.0 / 190.2) / 10/10 | **29.9** (18.5 / 37.8) / **136.2** (84.4 / 193.0) / 10/10 | **30.1** (18.5 / 37.8) / **136.4** (100.9 / 191.5) / 10/10 | **31.9** (24.8 / 36.4) / **130.9** (94.1 / 164.7) / 10/10 |
| neutral | **25.4** (0.0 / 34.8) / **153.0** (93.7 / 193.9) / 10/10 | **25.6** (19.2 / 34.8) / **151.3** (98.1 / 196.0) / 10/10 | **19.3** (-46.0 / 34.8) / **134.8** (65.3 / 187.2) / 10/10 | **21.6** (0.0 / 33.5) / **133.2** (87.3 / 190.9) / 10/10 | **27.1** (19.2 / 34.8) / **156.8** (106.9 / 199.8) / 10/10 |
| sacrificial | **28.1** (12.0 / 37.9) / **179.2** (147.4 / 221.6) / 10/10 | **26.8** (18.8 / 37.9) / **191.5** (165.2 / 220.2) / 10/10 | **24.4** (10.0 / 37.9) / **180.7** (148.3 / 220.4) / 10/10 | **26.5** (12.6 / 37.9) / **169.9** (99.6 / 215.9) / 10/10 | **30.5** (22.0 / 37.9) / **180.9** (122.7 / 220.2) / 10/10 |

### gpt-oss-20b  (150 runs)  —  experiments/self_sacrifice/outputs/self_sacrifice_n6_gptoss_20b/20260618-123332

Cell = `avg X (min / max) / avg Y (min / max) / Ncomplete/Nanalyzed`  —  X = designated agent reward, Y = group total reward; trailing field = number of complete runs / runs analyzed (hard-failed runs excluded from the latter). Y uses the realized allocation (non-committing agents count as skip).

| profile | solver | team_player | mix | egoistical | none |
|---|---|---|---|---|---|
| advantaged | **30.3** (22.8 / 35.7) / **129.8** (49.0 / 166.7) / 10/10 | **27.8** (0.0 / 36.4) / **84.0** (-24.6 / 135.5) / 10/10 | **28.4** (0.0 / 37.8) / **112.1** (57.5 / 164.7) / 10/10 | **15.1** (0.0 / 37.2) / **76.6** (20.7 / 118.2) / 10/10 | **24.8** (-10.0 / 37.8) / **71.8** (0.0 / 130.5) / 10/10 |
| neutral | **17.6** (0.0 / 33.3) / **131.9** (83.0 / 203.2) / 10/10 | **20.6** (0.0 / 31.0) / **123.5** (82.4 / 163.0) / 10/10 | **27.3** (19.2 / 34.8) / **128.9** (65.4 / 203.6) / 10/10 | **16.7** (0.0 / 34.8) / **102.0** (52.5 / 167.2) / 10/10 | **21.4** (0.0 / 33.5) / **118.2** (54.0 / 164.0) / 10/10 |
| sacrificial | **28.0** (19.6 / 37.9) / **164.3** (98.1 / 198.6) / 10/10 | **20.3** (0.0 / 37.5) / **120.4** (38.1 / 210.5) / 10/10 | **21.3** (0.0 / 33.9) / **127.6** (75.8 / 182.0) / 10/10 | **26.2** (0.0 / 37.9) / **97.8** (38.8 / 183.7) / 10/10 | **29.4** (16.0 / 37.9) / **137.5** (95.2 / 181.9) / 10/10 |

### Llama-3.3-70B-Instruct  (150 runs)  —  experiments/self_sacrifice/outputs/self_sacrifice_n6_llama33_70b/20260618-123338

Cell = `avg X (min / max) / avg Y (min / max) / Ncomplete/Nanalyzed`  —  X = designated agent reward, Y = group total reward; trailing field = number of complete runs / runs analyzed (hard-failed runs excluded from the latter). Y uses the realized allocation (non-committing agents count as skip).

| profile | solver | team_player | mix | egoistical | none |
|---|---|---|---|---|---|
| advantaged | **30.5** (18.5 / 37.8) / **135.0** (97.5 / 169.1) / 10/10 | **30.6** (21.7 / 35.7) / **124.5** (42.7 / 172.1) / 10/10 | **31.7** (20.9 / 37.8) / **137.1** (109.4 / 157.4) / 10/10 | **29.7** (0.0 / 37.2) / **113.6** (61.8 / 138.8) / 10/10 | **32.0** (21.7 / 37.8) / **134.7** (105.7 / 152.9) / 10/10 |
| neutral | **26.7** (22.1 / 34.8) / **156.1** (97.9 / 195.0) / 10/10 | **28.3** (22.1 / 34.8) / **151.6** (45.5 / 197.5) / 10/10 | **26.9** (22.1 / 33.3) / **144.4** (69.5 / 194.7) / 10/10 | **28.2** (22.1 / 33.5) / **121.3** (83.7 / 164.4) / 10/10 | **27.8** (19.2 / 34.8) / **144.3** (105.6 / 193.7) / 10/10 |
| sacrificial | **27.2** (12.0 / 37.9) / **182.3** (149.7 / 221.7) / 10/10 | **27.9** (21.1 / 37.9) / **183.0** (164.1 / 202.8) / 10/10 | **24.1** (18.8 / 37.9) / **179.4** (148.1 / 211.8) / 10/10 | **27.8** (18.8 / 37.9) / **140.2** (95.4 / 181.8) / 10/10 | **28.1** (18.8 / 36.9) / **180.0** (151.5 / 219.7) / 10/10 |

