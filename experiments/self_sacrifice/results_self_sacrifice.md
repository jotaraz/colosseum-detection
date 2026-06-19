# Self-sacrifice results

## Legend

**Setup.** Each model solves a JIRA-ticket DCOP: 6 agents coordinate over a shared blackboard to assign 8 micro-tasks, maximizing a joint reward (task-completion + priority bonuses, minus each agent's private task cost, minus penalties for duplicate claims). One agent is the **designated** agent (fixed identity, index 0).

**Profiles** — the designated agent's individual reward at the *global optimum*, relative to the other agents:
- `advantaged` — clearly higher than the others
- `neutral` — close to the others' mean
- `sacrificial` — clearly lower than the others (the optimum is a self-sacrifice for it)

**Framings** (the same underlying cost table, presented two ways):
- `solver` — an abstract, fully-anonymized DCOP (task variables T1..T8, solver nodes N1..N6, scalar costs); no real-world narrative.
- `JIRA` (personified) — the full JIRA narrative plus a persona/stakes block ("this cost is a real hardship for the person you represent; you may decline").

**Seeds.** 30 seeds per profile; each seed is a distinct cost table, run under *both* framings (so every cost table appears once as solver and once as JIRA).

**Cell** = `avg X (min / max) / avg Y (min / max) / complete/analyzed`:
- **X** = the designated agent's realized reward.
- **Y** = the group's total realized reward (= sum of all agents' realized rewards).
- **complete/analyzed** = runs where all 6 agents committed a decision / runs with usable data (30 minus any hard failures). Aggregates are over all analyzed runs; for incomplete runs Y is computed from the realized allocation, with non-committing agents counted as skip.

**Models.** Qwen2.5-72B and Llama-3.3-70B are standard instruct models and complete reliably (30/30). gpt-oss models are reasoning models served via the harmony format; gpt-oss-120b's JIRA runs often finish incomplete (it deliberates without emitting a commit), so its JIRA column is directional rather than fully clean.


### gpt-oss-120b  (180 runs)  —  experiments/self_sacrifice/outputs/self_sacrifice_n6_gptoss_120b/20260618-085924

Cell = `avg X (min / max) / avg Y (min / max) / Ncomplete/Nanalyzed`  —  X = designated agent reward, Y = group total reward; trailing field = number of complete runs / runs analyzed (hard-failed runs excluded from the latter). Y uses the realized allocation (non-committing agents count as skip).

| profile | solver | JIRA |
|---|---|---|
| advantaged | 33.2 (20.4 / 39.1) / 159.4 (114.4 / 198.7) / 30/30 | 27.0 (0.0 / 39.1) / 114.5 (63.2 / 171.5) / 14/30 |
| neutral | 29.0 (21.8 / 39.0) / 156.0 (100.8 / 213.1) / 30/30 | 26.3 (0.0 / 38.1) / 106.9 (64.0 / 169.8) / 12/30 |
| sacrificial | 26.7 (18.5 / 38.1) / 175.2 (116.4 / 223.5) / 30/30 | 24.1 (2.0 / 37.9) / 121.2 (48.2 / 189.5) / 10/30 |

### gpt-oss-20b  (145 runs)  —  experiments/self_sacrifice/outputs/self_sacrifice_n6_gptoss_20b/20260618-101906

Cell = `avg X (min / max) / avg Y (min / max) / Ncomplete/Nanalyzed`  —  X = designated agent reward, Y = group total reward; trailing field = number of complete runs / runs analyzed (hard-failed runs excluded from the latter). Y uses the realized allocation (non-committing agents count as skip).

| profile | solver | JIRA |
|---|---|---|
| advantaged | 28.9 (0.0 / 39.0) / 146.1 (96.6 / 198.5) / 30/30 | 28.6 (0.0 / 39.1) / 109.5 (28.5 / 167.5) / 19/20 |
| neutral | 24.6 (-24.0 / 39.0) / 144.6 (69.7 / 213.1) / 30/30 | 21.3 (0.0 / 35.4) / 112.3 (27.6 / 203.3) / 18/20 |
| sacrificial | 18.6 (-1.6 / 36.2) / 159.2 (90.8 / 214.6) / 30/30 | 24.5 (0.0 / 37.5) / 142.0 (92.4 / 217.0) / 14/15 |

### Llama-3.3-70B-Instruct  (180 runs)  —  experiments/self_sacrifice/outputs/self_sacrifice_n6_llama33_70b/20260618-101908

Cell = `avg X (min / max) / avg Y (min / max) / Ncomplete/Nanalyzed`  —  X = designated agent reward, Y = group total reward; trailing field = number of complete runs / runs analyzed (hard-failed runs excluded from the latter). Y uses the realized allocation (non-committing agents count as skip).

| profile | solver | JIRA |
|---|---|---|
| advantaged | 31.1 (18.5 / 39.1) / 148.8 (91.1 / 198.4) / 30/30 | 25.9 (0.0 / 39.1) / 93.7 (0.0 / 161.7) / 30/30 |
| neutral | 28.7 (20.6 / 38.7) / 153.6 (50.1 / 205.1) / 30/30 | 21.6 (0.0 / 39.0) / 108.8 (55.9 / 167.0) / 30/30 |
| sacrificial | 24.6 (8.0 / 37.9) / 166.4 (68.3 / 220.1) / 30/30 | 12.8 (0.0 / 38.1) / 111.0 (0.0 / 181.9) / 30/30 |

### Qwen2.5-72B-Instruct  (180 runs)  —  experiments/self_sacrifice/outputs/self_sacrifice_n6_qwen25_72b/20260618-023245

Cell = `avg X (min / max) / avg Y (min / max) / Ncomplete/Nanalyzed`  —  X = designated agent reward, Y = group total reward; trailing field = number of complete runs / runs analyzed (hard-failed runs excluded from the latter). Y uses the realized allocation (non-committing agents count as skip).

| profile | solver | JIRA |
|---|---|---|
| advantaged | 30.3 (5.0 / 39.1) / 149.6 (44.0 / 195.5) / 30/30 | 27.3 (0.0 / 39.0) / 116.3 (66.5 / 170.2) / 30/30 |
| neutral | 26.5 (0.0 / 39.0) / 140.8 (92.9 / 213.9) / 30/30 | 23.9 (0.0 / 39.0) / 122.4 (45.9 / 187.2) / 30/30 |
| sacrificial | 24.4 (12.0 / 38.1) / 156.3 (105.2 / 223.5) / 30/30 | 24.9 (10.1 / 38.1) / 139.4 (80.3 / 182.6) / 30/30 |

