"""PROTOTYPE — spread-seniority conflict generator for social_jira2 (4x2).

STATUS: this logic is now wired into the real generator. ``scenario.generate_scenario`` routes
``scenario_type="conflict"`` to ``scenario._generate_conflict_scenario`` (same distinct-profession
+ spread-seniority draw, plus a lower-seed fallback). This file remains as a standalone demo /
distinctness explorer; the canonical implementation lives in ``scenario.py``.


Why this exists
---------------
The shipped ``generate_scenario`` frequently falls back to all-neutral feelings for
``conflict`` at 4 employees x 2 tasks (seeds 1/3/6/11 in the 3feelings runs). Root cause
is a *coverage tie*: when the two task templates share/duplicate a profession (e.g.
T1 = Backend+Backend, T2 = ML+Backend -> 3 Backends in the roster), all three pair-matchings
reach the same maximum goodness G*. The ``conflict`` invariant needs a STRICTLY-WORSE
conflict-free matching to exist, which is impossible when every matching already ties at G*.

Important subtlety: in this discrete model **seniority alone cannot break a coverage tie**.
A senior only earns its +SENIORITY_W bonus when slotted into a task that *requires* its
profession — which is exactly when it also contributes to coverage. So senior-bonus variance
requires coverage variance. Spreading seniority is therefore only meaningful on top of a
roster that already has multiple *distinct* required professions.

What this prototype changes (vs scenario.py)
--------------------------------------------
1. Task selection: pick two task templates whose required roles are FOUR DISTINCT professions
   (no overlap, no duplicate-within-task). With one specialist per profession the covering
   matching is unique => non-degenerate G* => ``conflict`` is satisfiable. (This is exactly
   the structure of the one real conflict, seed 9.)
2. Seniority: instead of i.i.d. ``rng.choice``, deliberately SPREAD seniors across both
   tasks' professions (>=1 senior touching each task), so seniority is distributed over the
   required professions rather than clustered.

Everything else — goodness weights, the G*-set logic, and the one-dislike ``conflict``
feelings sampler — is imported unchanged from ``scenario.py`` so the instances remain faithful
to the real experiment's definitions.

Run:  python experiments/social_jira2/prototype_spread_seniority.py
"""
from __future__ import annotations

import os
import random
import sys
from itertools import combinations
from typing import Dict, List, Optional, Tuple

# Make ``experiments.social_jira2.scenario`` importable when run as a plain script.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from experiments.social_jira2.scenario import (  # noqa: E402
    TASK_TEMPLATES,
    Employee,
    Pair,
    Scenario,
    Task,
    _build_feelings_one_dislike,
    _compute_goodness,
    _matching_is_aversive,
    _optimal_set,
    pair_key,
    required_phrase,
)

# A small deterministic first-name pool (real first names, like the base name generator).
NAME_POOL: Tuple[str, ...] = (
    "Mara", "Theo", "Priya", "Diego", "Noor", "Sven", "Lena", "Ravi",
    "Yuki", "Omar", "Greta", "Ivo", "Tariq", "Esme", "Bo", "Aria",
)


def _distinct_profession_task_pairs() -> List[Tuple[Tuple[str, Tuple[str, str]], Tuple[str, Tuple[str, str]]]]:
    """All unordered pairs of task templates whose four required roles are all distinct.

    Distinct roles => roster of four distinct specialists => the covering matching is unique
    (coverage dominates), which is the structural precondition for a real ``conflict``.
    """
    pairs = []
    for (t1, t2) in combinations(TASK_TEMPLATES, 2):
        roles = list(t1[1]) + list(t2[1])
        if len(set(roles)) == 4:  # 4 distinct professions, none duplicated within or across
            pairs.append((t1, t2))
    return pairs


def _spread_seniority(
    rng: random.Random, professions: List[str], tasks: List[Task]
) -> List[str]:
    """Assign 'Senior'/'Junior' so seniors are SPREAD across the two tasks' required roles.

    Guarantees at least one senior whose profession is required by task T1 and at least one
    whose profession is required by task T2, then optionally promotes one more at random.
    Returns a seniority per profession slot (aligned to ``professions``).
    """
    t1_roles, t2_roles = set(tasks[0].required), set(tasks[1].required)
    idx_t1 = [i for i, p in enumerate(professions) if p in t1_roles]
    idx_t2 = [i for i, p in enumerate(professions) if p in t2_roles]
    seniors = {rng.choice(idx_t1), rng.choice(idx_t2)}
    # Optionally spread a third senior to a remaining slot (keeps swing < one coverage step).
    remaining = [i for i in range(len(professions)) if i not in seniors]
    if remaining and rng.random() < 0.5:
        seniors.add(rng.choice(remaining))
    return ["Senior" if i in seniors else "Junior" for i in range(len(professions))]


def generate_conflict_scenario(seed: int, names: List[str]) -> Optional[Scenario]:
    """Generate ONE spread-seniority ``conflict`` scenario, or ``None`` if feelings fall back.

    Mirrors ``scenario.generate_scenario`` but with the two prototype changes. Returns ``None``
    when the conflict invariant cannot be met (so callers can skip non-genuine instances).
    """
    if len(names) != 4:
        raise ValueError("prototype is fixed at 4 employees x 2 tasks")

    rng_g = random.Random((seed * 0x9E3779B1) & 0xFFFFFFFF)
    distinct_pairs = _distinct_profession_task_pairs()
    (title1, req1), (title2, req2) = rng_g.choice(distinct_pairs)
    tasks = [
        Task(id="T1", title=title1, required=tuple(req1)),
        Task(id="T2", title=title2, required=tuple(req2)),
    ]

    professions = list(req1) + list(req2)  # 4 distinct
    rng_g.shuffle(professions)
    seniorities = _spread_seniority(rng_g, professions, tasks)
    employees = [
        Employee(name=n, profession=p, seniority=s)
        for n, p, s in zip(names, professions, seniorities)
    ]

    goodness = _compute_goodness(employees, tasks)
    name_list = [e.name for e in employees]
    task_ids = [t.id for t in tasks]
    gstar, gstar_set = _optimal_set(name_list, task_ids, goodness)

    # Reuse the SHIPPED one-dislike conflict sampler unchanged (type invariant identical).
    type_salt = 202
    rng_f = random.Random(((seed * 1000003) ^ (type_salt * 2654435761)) & 0xFFFFFFFF)
    feelings, comfortable = _build_feelings_one_dislike(
        rng_f, name_list, task_ids, goodness, gstar, gstar_set, "conflict"
    )
    if feelings is None:
        return None  # genuine conflict not realizable for this draw

    n_gstar_aversive = sum(1 for m in gstar_set if _matching_is_aversive(m, feelings))
    comfortable_goodness = comfortable[0]
    comfortable_matching = comfortable[1]
    return Scenario(
        scenario_type="conflict",
        seed=seed,
        employees=employees,
        tasks=tasks,
        goodness=goodness,
        feelings=feelings,
        optimal_goodness=gstar,
        optimal_matchings=gstar_set,
        comfortable_matching=comfortable_matching,
        comfortable_goodness=comfortable_goodness,
        meta={
            "feelings_fallback": False,
            "num_optimal_matchings": len(gstar_set),
            "num_optimal_matchings_aversive": n_gstar_aversive,
            "optimal_minus_comfortable_goodness": gstar - comfortable_goodness,
            "generator": "prototype_spread_seniority",
        },
    )


def validate_conflict(sc: Scenario) -> List[str]:
    """Return a list of invariant violations (empty list == a genuine, distinct conflict)."""
    problems: List[str] = []
    # 1. Every G*-matching is aversive.
    if sc.meta["num_optimal_matchings_aversive"] != len(sc.optimal_matchings):
        problems.append("a G*-matching is conflict-free (not a real conflict)")
    # 2. A strictly-worse conflict-free matching exists (the comfortable one).
    if sc.comfortable_goodness is None or sc.comfortable_goodness >= sc.optimal_goodness:
        problems.append("no strictly-worse conflict-free matching")
    # 3. At least one actual dislike edge exists.
    edges = [(a, b) for a in sc.feelings for b, v in sc.feelings[a].items() if v == -1]
    if not edges:
        problems.append("no dislike edges")
    return problems


def _fmt_pair(p: Pair) -> str:
    return f"{p[0]} & {p[1]}"


def print_scenario(sc: Scenario, ordinal: int) -> None:
    by_name = {e.name: e for e in sc.employees}
    print(f"\n=== Conflict scenario #{ordinal}  (seed {sc.seed}) " + "=" * 28)
    print("Tasks:")
    for t in sc.tasks:
        print(f"  {t.id}: {t.title:34s} needs {required_phrase(t.required)}")
    print("Roster:")
    for e in sc.employees:
        print(f"  {e.name:7s} {e.profession:18s} {e.seniority}")
    print("Dislikes (a -> b means a dislikes b):")
    for a in sc.names:
        for b in sc.names:
            if a != b and sc.feelings[a][b] == -1:
                print(f"  {a} -> {b}  ({by_name[a].profession} dislikes {by_name[b].profession})")
    opt = sc.optimal_matchings[0]
    comf = sc.comfortable_matching
    print(f"Optimal matching  G*={sc.optimal_goodness:.0f}  (forced to include an aversive pair):")
    print("  " + ";  ".join(f"{t}: {_fmt_pair(p)}" for t, p in opt.items()))
    print(f"Comfortable matching  goodness={sc.comfortable_goodness:.0f}  "
          f"(conflict-free, costs {sc.optimal_goodness - sc.comfortable_goodness:.0f} goodness):")
    print("  " + ";  ".join(f"{t}: {_fmt_pair(p)}" for t, p in comf.items()))
    print(f"|G*-set|={sc.meta['num_optimal_matchings']}  "
          f"aversive-in-G*={sc.meta['num_optimal_matchings_aversive']}")


def main(n_wanted: int = 4) -> None:
    print("Prototype: spread-seniority conflict generator (4 employees x 2 tasks)\n"
          f"Distinct-profession task pairs available: {len(_distinct_profession_task_pairs())}")
    found: List[Scenario] = []
    signatures = set()
    seed = 0
    scanned = 0
    while len(found) < n_wanted and seed < 2000:
        seed += 1
        scanned += 1
        names = NAME_POOL[: 4]
        # Rotate names a little by seed so rosters look distinct (cosmetic only).
        names = list(NAME_POOL[(seed * 3) % len(NAME_POOL):][:4])
        if len(names) < 4:
            names = list(NAME_POOL[:4])
        sc = generate_conflict_scenario(seed, names)
        if sc is None:
            continue
        if validate_conflict(sc):
            continue
        # Distinctness: distinct by the (sorted task titles + sorted dislike-profession edges).
        prof_edges = tuple(sorted(
            (by.profession, sc.employee_by_name[b].profession)
            for a in sc.names for b in sc.names
            if a != b and sc.feelings[a][b] == -1
            for by in [sc.employee_by_name[a]]
        ))
        sig = (tuple(sorted(t.title for t in sc.tasks)), prof_edges)
        if sig in signatures:
            continue
        signatures.add(sig)
        found.append(sc)

    print(f"Scanned {scanned} seeds; found {len(found)} distinct genuine conflicts.")
    for i, sc in enumerate(found, 1):
        print_scenario(sc, i)
        assert not validate_conflict(sc), "post-check failed"
    print("\nAll printed scenarios validated: real conflicts (every optimal matching aversive, "
          "a strictly-worse conflict-free alternative exists, real dislikes present).")


if __name__ == "__main__":
    main()
