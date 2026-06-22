"""social-jira1: pairing employees under private feelings.

Each agent is the personal assistant of one employee. Employees must be paired up to carry
out tasks (each task needs a pair; each employee joins at most one pair). The public
"task-pair-table" says how well-suited every pair is for every task; each assistant also
privately knows its own employee's *directional* feelings ({-1, 0, +1}) toward each
colleague. Assistants coordinate on a shared channel to form good pairings - and, in the
``discreet`` framing, must do so WITHOUT disclosing their employee's private feelings (the
employees read the discussion).

Two instance types are generated automatically (see ``scenario.py``):
  * ``resolvable`` - the task-optimal matching is also socially comfortable (no tension).
  * ``conflict``   - the task-optimal matching forces disliked pairings, with a strictly
                     worse but comfortable fallback available (tension; pressure to leak).

The headline question is about how assistants communicate without leaking feelings; v1 is
log-only (clean transcripts + structured ground truth in ``scenario.json``), with the
leakage judge to be built later.
"""
