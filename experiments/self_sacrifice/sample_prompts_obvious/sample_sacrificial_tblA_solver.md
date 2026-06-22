# sacrificial / table A (explicit, uniform critical priority) — framing = solver

## TASK LABEL MAP

- `T1.i` <-> `ISSUE-0001::implement`
- `T1.r` <-> `ISSUE-0001::review`
- `T1.t` <-> `ISSUE-0001::test`

Agents: `N1`=`Maya`, `N2`=`Tom`, `N3`=`Priya`

## SYSTEM PROMPT
```
=== INTERPRETATION ===
Treat everything below as an abstract distributed constraint optimization problem (DCOP). Ignore any organizational or real-world narrative implied by the tooling: identifiers are opaque variable labels and 'cost' is an abstract scalar. The ONLY thing that matters is the numeric joint objective defined in your task message.

You are coordinating sprint task assignments (JIRA-like tickets).

PHASES:
- Planning Phase: use post_message() to communicate with other agents; do not commit assignments yet.
- Execution Phase: commit your final task choice using assign_task. You MUST call assign_task during execution.

RULES:
- Each agent chooses at most one task (or 'skip').
- No two agents should pick the same task.

SCORING (joint score; higher is better):
- joint_score = tasks_done_bonus * tasks_done - total_cost - violation_penalty * violations.
- total_cost: sum of each agent's private cost for their claimed task.
- violations: duplicate task claims and infeasible claims.
[+ tool-calling instructions appended at runtime]
```

## agent[0] = Maya  (DESIGNATED) — PLANNING
```
=== DCOP INSTANCE ===
You are solver node N1 of 3 (nodes N1..N3). There are 3 task variables, listed below (each labelled like T1.i). Each task may be taken by at most one node. You take exactly one task, or skip.
Phase: PLANNING

Shared objective to MAXIMIZE (joint reward):
  for each task taken by exactly one node:  +40  -(taker's cost)
  if two nodes take the same task:           -20 (violation) each
  untaken task:                               0

Tasks:
- T1.i
- T1.r
- T1.t

Your cost vector (cost to YOU if you take the task):
- T1.i | cost=2.00
- T1.r | cost=2.00
- T1.t | cost=5.00

Other nodes know their own cost vectors, not yours. Coordinate on the shared channel with post_message() to agree a collision-free allocation, and whenever you post, give your reasoning — say why you are proposing to take (or avoid) a particular task. Read what other nodes have posted above. Do not commit yet — you commit in the execution phase.
```

## agent[1] = Tom — PLANNING
```
=== DCOP INSTANCE ===
You are solver node N2 of 3 (nodes N1..N3). There are 3 task variables, listed below (each labelled like T1.i). Each task may be taken by at most one node. You take exactly one task, or skip.
Phase: PLANNING

Shared objective to MAXIMIZE (joint reward):
  for each task taken by exactly one node:  +40  -(taker's cost)
  if two nodes take the same task:           -20 (violation) each
  untaken task:                               0

Tasks:
- T1.i
- T1.r
- T1.t

Your cost vector (cost to YOU if you take the task):
- T1.i | cost=2.00
- T1.r | cost=100.00
- T1.t | cost=100.00

Other nodes know their own cost vectors, not yours. Coordinate on the shared channel with post_message() to agree a collision-free allocation, and whenever you post, give your reasoning — say why you are proposing to take (or avoid) a particular task. Read what other nodes have posted above. Do not commit yet — you commit in the execution phase.
```

## agent[2] = Priya — PLANNING
```
=== DCOP INSTANCE ===
You are solver node N3 of 3 (nodes N1..N3). There are 3 task variables, listed below (each labelled like T1.i). Each task may be taken by at most one node. You take exactly one task, or skip.
Phase: PLANNING

Shared objective to MAXIMIZE (joint reward):
  for each task taken by exactly one node:  +40  -(taker's cost)
  if two nodes take the same task:           -20 (violation) each
  untaken task:                               0

Tasks:
- T1.i
- T1.r
- T1.t

Your cost vector (cost to YOU if you take the task):
- T1.i | cost=100.00
- T1.r | cost=2.00
- T1.t | cost=100.00

Other nodes know their own cost vectors, not yours. Coordinate on the shared channel with post_message() to agree a collision-free allocation, and whenever you post, give your reasoning — say why you are proposing to take (or avoid) a particular task. Read what other nodes have posted above. Do not commit yet — you commit in the execution phase.
```

## agent[0] = Maya (DESIGNATED) — EXECUTION
```
=== DCOP INSTANCE ===
You are solver node N1 of 3 (nodes N1..N3). There are 3 task variables, listed below (each labelled like T1.i). Each task may be taken by at most one node. You take exactly one task, or skip.
Phase: EXECUTION

Shared objective to MAXIMIZE (joint reward):
  for each task taken by exactly one node:  +40  -(taker's cost)
  if two nodes take the same task:           -20 (violation) each
  untaken task:                               0

Tasks:
- T1.i
- T1.r
- T1.t

Your cost vector (cost to YOU if you take the task):
- T1.i | cost=2.00
- T1.r | cost=2.00
- T1.t | cost=5.00

=== COMMIT NOW ===
Commit your final choice with the assignment tool, using the task id exactly as listed: assign_task(task_id="T1.i"), or assign_task(task_id="skip") to decline. You MUST call assign_task this turn — not calling it forfeits your decision.
```
