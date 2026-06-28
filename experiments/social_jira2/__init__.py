"""social-jira2: pairing employees under private feelings, with role-driven task fit.

Successor to social_jira1. Each agent is the personal assistant of one employee. Employees
must be paired up to carry out tasks (each task needs a pair; each employee joins at most one
pair). Unlike jira1, fit is **role-driven**: each employee has a profession and seniority,
each task has a required pair of professions, and the public goodness is a deterministic
function of the two (coverage dominates a smaller seniority bonus; ties are allowed). Agents
see the roster and requirements — never the numbers — and must infer fit. Each assistant also
privately knows its own employee's *directional* feelings ({-1, 0} — dislike or neutral)
toward each colleague, and (under the ``default`` confidentiality preset) must coordinate
without disclosing them (the employees read the discussion).

Two instance types are generated automatically (see ``scenario.py``), defined in goodness
terms so they are robust to ties (``G*`` = max total goodness):
  * ``resolvable`` - at least one G*-matching is conflict-free (optimal is also comfortable).
  * ``conflict``   - every G*-matching is aversive, with a strictly-worse comfortable
                     fallback (tension; pressure to leak).

Variability axes (SPEC.md): feelings presets (§4), assistant personality (§5), and an
optional decoy block (§6, deferred). The base build ships the ``default``/``control`` presets
(byte-identical to jira1's discreet/control system + feelings blocks) and the full uniform
personality registry; ``mixed`` personality, decoys, and the soft presets are deferred hooks.

The headline question is how assistants communicate without leaking feelings; v2 stays
log-only (clean transcripts + structured ground truth in ``scenario.json``), with the leakage
judge to be built later.
"""
