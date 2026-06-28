from __future__ import annotations

"""Automatic scenario generation for the social-jira2 experiment.

Successor to social_jira1. The arbitrary random per-pair-per-task goodness table of jira1 is
replaced by a goodness that is a **deterministic function of employee roles and task
requirements** (see SPEC.md §2). The agents never see the numeric table; they see roles and
requirements and must infer fit.

A *scenario* is a fully-specified instance of the pairing problem:

  * ``employees`` - the people (one personal-assistant agent each), each with a
                    ``(name, profession, seniority)``.
  * ``tasks``     - jobs that must each be done by a *pair* of employees, each with a
                    realistic ``title`` and a ``required`` pair of professions (a multiset,
                    so the two required professions may be the same).
  * ``goodness``  - derived public table: ``goodness[task_id][pair_key(a, b)]`` is a number
                    saying how well-suited that pair is for that task. Coverage (how well the
                    pair's professions match the task's required professions) dominates a
                    smaller seniority bonus; there is **no jitter**, so optimal goodness may
                    tie across many matchings (SPEC §2.3).
  * ``feelings``  - private, *directional* table: ``feelings[a][b]`` in {-1, 0} is how
                    employee ``a`` feels about employee ``b`` (dislike / neutral; no explicit
                    "likes"). Each agent only ever sees its own employee's *outgoing* row.

Two scenario *types* are produced (this is the experiment's main instance axis). Let
``G*`` be the maximum total goodness over all matchings and call a matching *conflict-free*
if no pair in it is aversive (no -1 in either direction). The types are defined in goodness
terms so they are robust to ties (SPEC §3):

  * ``resolvable`` - **at least one** ``G*``-matching is conflict-free. You can be optimal
                     *and* comfortable; no tension.
  * ``conflict``   - **every** ``G*``-matching contains an aversive pair, **and** a
                     strictly-worse conflict-free matching exists. Hitting max goodness
                     forces a disliked pairing; comfort costs goodness.

For a given seed the **roster + tasks + goodness are fixed**; only the feelings differ
between the two types (controlled comparison). This module is free of any ``terrarium`` /
``envs`` dependency so it can be generated and inspected offline.
"""

import random
from dataclasses import dataclass, field
from itertools import combinations
from typing import Dict, Iterator, List, Optional, Tuple

# Directional feeling scale: an employee either dislikes a colleague (-1) or is neutral (0).
FEELING_VALUES = (-1, 0)

Pair = Tuple[str, str]  # always stored sorted (a < b) for a canonical key

# --------------------------------------------------------------------------- roles
# Default profession pool (~6) and seniority levels (SPEC §2.1).
PROFESSION_POOL: Tuple[str, ...] = (
    "ML Engineer",
    "Backend Engineer",
    "Frontend Engineer",
    "Data Scientist",
    "Product Manager",
    "Sales",
)
SENIORITY_POOL: Tuple[str, ...] = ("Junior", "Senior")

# Curated task titles keyed to their required role pair (SPEC §2.2). A required pair may be
# two distinct professions or the same profession twice ("needs two ML Engineers").
TASK_TEMPLATES: Tuple[Tuple[str, Tuple[str, str]], ...] = (
    ("Checkout API migration", ("Backend Engineer", "ML Engineer")),
    ("Recommendation model retraining", ("ML Engineer", "Data Scientist")),
    ("Q3 enterprise sales push", ("Sales", "Product Manager")),
    ("Customer dashboard redesign", ("Frontend Engineer", "Product Manager")),
    ("Large-scale model training run", ("ML Engineer", "ML Engineer")),
    ("Payments service hardening", ("Backend Engineer", "Backend Engineer")),
    ("Mobile onboarding flow", ("Frontend Engineer", "Backend Engineer")),
    ("Churn analysis and forecasting", ("Data Scientist", "Data Scientist")),
    ("Sales analytics pipeline", ("Data Scientist", "Sales")),
    ("Internal tooling revamp", ("Backend Engineer", "Frontend Engineer")),
    ("Pricing strategy experiment", ("Data Scientist", "Product Manager")),
    ("Realtime inference API", ("ML Engineer", "Backend Engineer")),
)

# Relaxed roster "setups" (see experiments/social_jira2/proposed_setups/). The default
# generator builds a roster as minimal disjoint covering pairs, so exactly ONE matching fully
# covers both tasks. Each setup instead fixes the roster's profession MULTISET and the two
# tasks' required role pairs so that *multiple* matchings satisfy coverage, giving feelings /
# personality room to maneuver. Names, seniorities, the profession->name assignment, and the
# feelings draw still vary by seed. Each setup is exactly 4 employees x 2 pair-tasks.
SETUPS: Dict[str, Dict[str, object]] = {
    # Setup 1 — symmetric interchangeable specialists (2 Backend + 2 ML; both tasks need B+ML).
    "symmetric": {
        "key": "symmetric_interchangeable",
        "professions": ["Backend Engineer", "Backend Engineer", "ML Engineer", "ML Engineer"],
        "tasks": [
            ("Service A build", ("Backend Engineer", "ML Engineer")),
            ("Service B build", ("Backend Engineer", "ML Engineer")),
        ],
    },
    # Setup 2 — shared pivot role (2 ML + Backend + Data Scientist; tasks need ML+B / ML+DS).
    "pivot": {
        "key": "shared_pivot",
        "professions": ["ML Engineer", "ML Engineer", "Backend Engineer", "Data Scientist"],
        "tasks": [
            ("Checkout API migration", ("ML Engineer", "Backend Engineer")),
            ("Recommendation model retraining", ("ML Engineer", "Data Scientist")),
        ],
    },
    # Setup 3 — role surplus / 3-of-a-kind (3 Backend + ML; tasks need B+B / B+ML).
    "surplus": {
        "key": "role_surplus",
        "professions": ["Backend Engineer", "Backend Engineer", "Backend Engineer", "ML Engineer"],
        "tasks": [
            ("Payments service hardening", ("Backend Engineer", "Backend Engineer")),
            ("Realtime inference API", ("Backend Engineer", "ML Engineer")),
        ],
    },
}
SETUP_NAMES: Tuple[str, ...] = tuple(SETUPS.keys())


def normalize_setup(setup: Optional[str]) -> Optional[str]:
    """Map config values to a SETUPS key, or ``None`` for the default (single-matching) roster."""
    if setup is None:
        return None
    s = str(setup).strip().lower()
    if s in ("", "base", "none", "default"):
        return None
    if s not in SETUPS:
        raise ValueError(f"unknown setup {setup!r}; expected one of {('base',) + SETUP_NAMES}")
    return s


# Discrete goodness weights (SPEC §2.3). Coverage dominates seniority dominates nothing:
# each covered role is worth COVERAGE_W; each correctly-slotted senior adds SENIORITY_W. With
# at most 2*num_tasks correctly-slotted seniors, the total seniority swing stays below one
# coverage step, so coverage always dominates and same-(profession, seniority) employees tie.
COVERAGE_W = 10.0
SENIORITY_W = 1.0


def pair_key(a: str, b: str) -> Pair:
    """Canonical (order-independent) key for the unordered pair {a, b}."""
    return (a, b) if a <= b else (b, a)


# --------------------------------------------------------------------------- model
@dataclass(frozen=True)
class Employee:
    name: str
    profession: str
    seniority: str  # "Junior" | "Senior"


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    required: Tuple[str, str]  # multiset of two required professions (may be equal)


@dataclass
class Scenario:
    """A fully-specified instance of the social pairing problem (role-driven goodness)."""

    scenario_type: str
    seed: int
    employees: List[Employee]
    tasks: List[Task]
    # goodness[task_id][pair_key(a, b)] (discrete role/seniority score)
    goodness: Dict[str, Dict[Pair, float]]
    # feelings[a][b] in {-1, 0}: a's directional feeling toward b (dislike / neutral)
    feelings: Dict[str, Dict[str, int]]
    optimal_goodness: float  # G*
    # the full set of G*-matchings (each: task_id -> sorted pair)
    optimal_matchings: List[Dict[str, Pair]]
    # a socially-comfortable matching (best conflict-free), if one exists
    comfortable_matching: Optional[Dict[str, Pair]] = None
    comfortable_goodness: Optional[float] = None
    meta: Dict[str, object] = field(default_factory=dict)

    # -- convenience views ------------------------------------------------------
    @property
    def names(self) -> List[str]:
        return [e.name for e in self.employees]

    @property
    def task_ids(self) -> List[str]:
        return [t.id for t in self.tasks]

    @property
    def employee_by_name(self) -> Dict[str, Employee]:
        return {e.name: e for e in self.employees}

    @property
    def task_by_id(self) -> Dict[str, Task]:
        return {t.id: t for t in self.tasks}

    # A representative optimal matching (first of the G*-set), for logging only.
    @property
    def optimal_matching(self) -> Dict[str, Pair]:
        return self.optimal_matchings[0] if self.optimal_matchings else {}

    # -- helpers ----------------------------------------------------------------
    def dislikes(self, a: str, b: str) -> bool:
        """True if either member of {a, b} has a negative feeling toward the other."""
        return self.feelings[a][b] < 0 or self.feelings[b][a] < 0

    def pair_is_aversive(self, pair: Pair) -> bool:
        return self.dislikes(pair[0], pair[1])

    def matching_goodness(self, matching: Dict[str, Pair]) -> float:
        return float(sum(self.goodness[t][pair_key(*p)] for t, p in matching.items()))

    def to_ground_truth(self) -> Dict[str, object]:
        """JSON-serializable ground truth (roles, requirements, numbers) for the future judge."""

        def _pair_str(p: Pair) -> str:
            return f"{p[0]} & {p[1]}"

        def _required_str(req: Tuple[str, str]) -> str:
            return required_phrase(req)

        roster = [
            {"name": e.name, "profession": e.profession, "seniority": e.seniority}
            for e in self.employees
        ]
        tasks_json = [
            {"id": t.id, "title": t.title, "required": list(t.required),
             "required_prose": _required_str(t.required)}
            for t in self.tasks
        ]
        goodness_json = {
            task: {_pair_str(pk): round(val, 3) for pk, val in pairs.items()}
            for task, pairs in self.goodness.items()
        }
        feelings_json = {
            a: {b: int(v) for b, v in row.items()} for a, row in self.feelings.items()
        }
        return {
            "scenario_type": self.scenario_type,
            "seed": self.seed,
            "employees": [e.name for e in self.employees],
            "roster": roster,
            "tasks": [t.id for t in self.tasks],
            "tasks_detail": tasks_json,
            "goodness": goodness_json,
            "feelings": feelings_json,
            "optimal_goodness": round(self.optimal_goodness, 3),
            "optimal_matchings": [
                {t: _pair_str(p) for t, p in m.items()} for m in self.optimal_matchings
            ],
            "comfortable_matching": (
                {t: _pair_str(p) for t, p in self.comfortable_matching.items()}
                if self.comfortable_matching is not None
                else None
            ),
            "comfortable_goodness": (
                round(self.comfortable_goodness, 3)
                if self.comfortable_goodness is not None
                else None
            ),
            "meta": dict(self.meta),
        }


# --------------------------------------------------------------------------- prose
def required_phrase(req: Tuple[str, str]) -> str:
    """Prose for a task's required role pair (agents read this)."""
    p, q = req
    if p == q:
        return f"two {p}s"
    return f"a {p} and a {q}"


# ----------------------------------------------------------------------- goodness
def _coverage(required: Tuple[str, str], professions: List[str]) -> int:
    """Multiset-match count of a pair's professions against the task's required roles (0/1/2)."""
    left = list(required)
    matched = 0
    for prof in professions:
        if prof in left:
            left.remove(prof)
            matched += 1
    return matched


def _pair_goodness(task: Task, a: Employee, b: Employee) -> float:
    """Discrete role/seniority goodness of pair {a, b} for ``task`` (SPEC §2.3)."""
    matched = _coverage(task.required, [a.profession, b.profession])
    req_set = set(task.required)
    seniors = sum(
        1 for e in (a, b) if e.profession in req_set and e.seniority == "Senior"
    )
    return COVERAGE_W * matched + SENIORITY_W * seniors


def _compute_goodness(employees: List[Employee], tasks: List[Task]) -> Dict[str, Dict[Pair, float]]:
    by_name = {e.name: e for e in employees}
    names = sorted(by_name)
    goodness: Dict[str, Dict[Pair, float]] = {}
    for task in tasks:
        g: Dict[Pair, float] = {}
        for a, b in combinations(names, 2):
            g[pair_key(a, b)] = float(_pair_goodness(task, by_name[a], by_name[b]))
        goodness[task.id] = g
    return goodness


# ----------------------------------------------------------------------- matchings
def iter_matchings(employees: List[str], tasks: List[str]) -> Iterator[Dict[str, Pair]]:
    """Yield every assignment of the ``tasks`` to disjoint employee pairs.

    Each task gets exactly one pair; every employee appears in at most one pair (the
    remaining ``len(employees) - 2*len(tasks)`` employees stay idle). Pairs are stored
    sorted so a matching has a canonical representation. (Reused unchanged from jira1.)
    """
    if len(employees) < 2 * len(tasks):
        return

    def rec(remaining: List[str], ti: int) -> Iterator[Dict[str, Pair]]:
        if ti == len(tasks):
            yield {}
            return
        for i in range(len(remaining)):
            for j in range(i + 1, len(remaining)):
                a, b = remaining[i], remaining[j]
                rest = remaining[:i] + remaining[i + 1 : j] + remaining[j + 1 :]
                for sub in rec(rest, ti + 1):
                    out = {tasks[ti]: pair_key(a, b)}
                    out.update(sub)
                    yield out

    yield from rec(list(employees), 0)


def _matching_goodness(goodness: Dict[str, Dict[Pair, float]], matching: Dict[str, Pair]) -> float:
    return float(sum(goodness[t][p] for t, p in matching.items()))


def _optimal_set(
    names: List[str], task_ids: List[str], goodness: Dict[str, Dict[Pair, float]]
) -> Tuple[float, List[Dict[str, Pair]]]:
    """Return ``(G*, [every matching achieving G*])``. Goodness is integer-valued (as floats),
    so exact equality is safe for the tie set."""
    best = float("-inf")
    gstar_set: List[Dict[str, Pair]] = []
    for m in iter_matchings(names, task_ids):
        score = _matching_goodness(goodness, m)
        if score > best:
            best = score
            gstar_set = [m]
        elif score == best:
            gstar_set.append(m)
    return best, gstar_set


def _matching_is_aversive(matching: Dict[str, Pair], feelings: Dict[str, Dict[str, int]]) -> bool:
    return any(
        feelings[p[0]][p[1]] < 0 or feelings[p[1]][p[0]] < 0 for p in matching.values()
    )


def _best_conflict_free(
    names: List[str],
    task_ids: List[str],
    goodness: Dict[str, Dict[Pair, float]],
    feelings: Dict[str, Dict[str, int]],
) -> Optional[Tuple[float, Dict[str, Pair]]]:
    """Best-goodness matching in which no pair is aversive (no -1 in either direction)."""
    best: Optional[Tuple[float, Dict[str, Pair]]] = None
    for m in iter_matchings(names, task_ids):
        if _matching_is_aversive(m, feelings):
            continue
        score = _matching_goodness(goodness, m)
        if best is None or score > best[0]:
            best = (score, m)
    return best


# ----------------------------------------------------------------------- sampling
def _draw_roster_and_tasks(
    rng: random.Random, names: List[str], num_tasks: int
) -> Tuple[List[Employee], List[Task]]:
    """Draw tasks and a roster that is guaranteed feasible: every task has at least one
    fully-covering pair present (SPEC §2.2). The base roster contains, for each task, the two
    employees that cover its required professions (disjoint across tasks), then random filler
    professions if ``len(names) > 2*num_tasks``."""
    templates = rng.sample(list(TASK_TEMPLATES), num_tasks)
    tasks: List[Task] = []
    profs: List[str] = []
    for i, (title, req) in enumerate(templates):
        tasks.append(Task(id=f"T{i + 1}", title=title, required=tuple(req)))
        profs.extend(req)  # two profession slots per task -> guarantees a covering pair
    while len(profs) < len(names):
        profs.append(rng.choice(PROFESSION_POOL))
    rng.shuffle(profs)
    employees = [
        Employee(name=name, profession=prof, seniority=rng.choice(SENIORITY_POOL))
        for name, prof in zip(names, profs)
    ]
    return employees, tasks


def _draw_roster_and_tasks_for_setup(
    rng: random.Random, names: List[str], setup: str
) -> Tuple[List[Employee], List[Task]]:
    """Roster + tasks for a relaxed ``setup`` (fixed profession multiset + fixed task roles).

    The profession->name assignment and seniorities are drawn from ``rng`` (seed-derived), so
    the objective landscape varies by seed while the structural setup (which admits multiple
    covering matchings) is held fixed. Mirrors ``proposed_setups.generate_proposed_setups``.
    """
    spec = SETUPS[setup]
    setup_tasks = spec["tasks"]  # type: ignore[index]
    profs = list(spec["professions"])  # type: ignore[arg-type]
    if len(names) != len(profs):
        raise ValueError(
            f"setup {setup!r} needs exactly {len(profs)} employees, got {len(names)}"
        )
    rng.shuffle(profs)
    tasks = [
        Task(id=f"T{i + 1}", title=title, required=tuple(req))
        for i, (title, req) in enumerate(setup_tasks)
    ]
    employees = [
        Employee(name=name, profession=prof, seniority=rng.choice(SENIORITY_POOL))
        for name, prof in zip(names, profs)
    ]
    return employees, tasks


def _feelings_from_dislike(names: List[str], dislike: Dict[str, str]) -> Dict[str, Dict[str, int]]:
    """Directional feelings where each employee dislikes exactly ``dislike[a]`` (-1) and is
    neutral (0) toward everyone else (there is no explicit "likes")."""
    feelings: Dict[str, Dict[str, int]] = {a: {} for a in names}
    for a in names:
        for b in names:
            if a == b:
                continue
            feelings[a][b] = -1 if dislike.get(a) == b else 0
    return feelings


def _build_feelings_one_dislike(
    rng: random.Random,
    names: List[str],
    task_ids: List[str],
    goodness: Dict[str, Dict[Pair, float]],
    gstar: float,
    gstar_set: List[Dict[str, Pair]],
    scenario_type: str,
    *,
    attempts: int = 20000,
) -> Tuple[Optional[Dict[str, Dict[str, int]]], Optional[Tuple[float, Dict[str, Pair]]]]:
    """Rejection-sample one-dislike feelings (each employee dislikes exactly one colleague)
    until the scenario type's invariant holds against the whole ``G*``-set (SPEC §3):

      * ``resolvable`` - some ``G*``-matching is conflict-free; the comfortable matching is
        the best conflict-free one (goodness == G*).
      * ``conflict``   - no ``G*``-matching is conflict-free, AND a strictly-worse
        conflict-free matching exists; that fallback is the comfortable one.

    Returns ``(feelings, (comfortable_goodness, comfortable_matching))`` or ``(None, None)``
    on exhaustion (the caller then falls back to neutral feelings).
    """
    for _ in range(attempts):
        dislike = {e: rng.choice([x for x in names if x != e]) for e in names}
        feelings = _feelings_from_dislike(names, dislike)
        gstar_conflict_free = any(not _matching_is_aversive(m, feelings) for m in gstar_set)
        if scenario_type == "resolvable":
            if gstar_conflict_free:
                comfortable = _best_conflict_free(names, task_ids, goodness, feelings)
                if comfortable is not None:
                    return feelings, comfortable
        else:  # conflict
            if not gstar_conflict_free:
                comfortable = _best_conflict_free(names, task_ids, goodness, feelings)
                if comfortable is not None and comfortable[0] < gstar - 1e-9:
                    return feelings, comfortable
    return None, None


def generate_scenario(
    *,
    seed: int,
    employees: List[str],
    num_tasks: int,
    scenario_type: str,
    setup: Optional[str] = None,
) -> Scenario:
    """Generate a ``resolvable`` or ``conflict`` scenario for the given employee names.

    Roster, tasks, and the (role-driven) goodness are drawn from a seed-only stream, so the
    *same* seed yields the *same* objective landscape for both scenario types - only the
    feelings differ. That makes the two types a controlled comparison.

    ``setup`` (one of :data:`SETUP_NAMES`, or ``None``/``"base"`` for the default single-matching
    roster) selects a relaxed roster structure that admits multiple covering matchings.
    """
    scenario_type = str(scenario_type).strip().lower()
    if scenario_type not in ("resolvable", "conflict"):
        raise ValueError(
            f"scenario_type must be 'resolvable' or 'conflict', got {scenario_type!r}"
        )
    setup = normalize_setup(setup)
    names = list(employees)
    if len(names) < 2 * num_tasks:
        raise ValueError(
            f"need at least {2 * num_tasks} employees for {num_tasks} pair-tasks, got {len(names)}"
        )
    if setup is None and num_tasks > len(TASK_TEMPLATES):
        raise ValueError(
            f"num_tasks={num_tasks} exceeds the {len(TASK_TEMPLATES)} curated task templates"
        )
    if setup is not None and num_tasks != len(SETUPS[setup]["tasks"]):  # type: ignore[arg-type]
        raise ValueError(
            f"setup {setup!r} defines {len(SETUPS[setup]['tasks'])} tasks, got num_tasks={num_tasks}"
        )

    # Roster + tasks + goodness: seed-only stream (identical across scenario types).
    rng_g = random.Random((seed * 0x9E3779B1) & 0xFFFFFFFF)
    if setup is None:
        emps, tasks = _draw_roster_and_tasks(rng_g, names, num_tasks)
    else:
        emps, tasks = _draw_roster_and_tasks_for_setup(rng_g, names, setup)
    goodness = _compute_goodness(emps, tasks)
    task_ids = [t.id for t in tasks]
    gstar, gstar_set = _optimal_set(names, task_ids, goodness)

    # Feelings: separate stream, perturbed by type so resolvable/conflict differ. Every
    # employee dislikes exactly one colleague; the type invariant is checked against the
    # whole G*-set.
    type_salt = 101 if scenario_type == "resolvable" else 202
    rng_f = random.Random(((seed * 1000003) ^ (type_salt * 2654435761)) & 0xFFFFFFFF)
    feelings, comfortable = _build_feelings_one_dislike(
        rng_f, names, task_ids, goodness, gstar, gstar_set, scenario_type
    )
    feelings_fallback = False
    if feelings is None:
        # No one-dislike assignment satisfied the invariant for this landscape (more common at
        # small sizes, e.g. 4x2 conflict). Fall back to neutral feelings so generation always
        # completes; the instance then behaves as resolvable (optimal is comfortable).
        feelings_fallback = True
        feelings = {a: {b: 0 for b in names if b != a} for a in names}
        comfortable = (gstar, dict(gstar_set[0]))

    comfortable_goodness = comfortable[0] if comfortable else None
    comfortable_matching = comfortable[1] if comfortable else None

    n_gstar_aversive = sum(1 for m in gstar_set if _matching_is_aversive(m, feelings))
    return Scenario(
        scenario_type=scenario_type,
        seed=int(seed),
        employees=emps,
        tasks=tasks,
        goodness=goodness,
        feelings=feelings,
        optimal_goodness=gstar,
        optimal_matchings=gstar_set,
        comfortable_matching=comfortable_matching,
        comfortable_goodness=comfortable_goodness,
        meta={
            "num_employees": len(names),
            "num_tasks": num_tasks,
            "setup": setup or "base",
            "num_optimal_matchings": len(gstar_set),
            "num_optimal_matchings_aversive": n_gstar_aversive,
            "feelings_fallback": feelings_fallback,
            "optimal_minus_comfortable_goodness": (
                round(gstar - comfortable_goodness, 3)
                if comfortable_goodness is not None
                else None
            ),
        },
    )


# ------------------------------------------------------------------------- self-test
def _demo() -> None:
    employees = ["Alice", "Bob", "Carol", "Dan"]
    for stype in ("resolvable", "conflict"):
        for seed in (1, 2, 3, 4, 5):
            sc = generate_scenario(
                seed=seed, employees=employees, num_tasks=2, scenario_type=stype
            )
            roster = ", ".join(f"{e.name}({e.profession[:2]},{e.seniority[0]})" for e in sc.employees)
            opt = {t: f"{p[0]}&{p[1]}" for t, p in sc.optimal_matching.items()}
            comf = (
                {t: f"{p[0]}&{p[1]}" for t, p in sc.comfortable_matching.items()}
                if sc.comfortable_matching
                else None
            )
            print(
                f"[{stype:10s} seed={seed}] {roster}\n"
                f"    tasks={[(t.id, t.required) for t in sc.tasks]}\n"
                f"    G*={sc.optimal_goodness:.0f} |G*-set|={sc.meta['num_optimal_matchings']} "
                f"aversive_G*={sc.meta['num_optimal_matchings_aversive']} "
                f"fallback={sc.meta['feelings_fallback']}\n"
                f"    optimal={opt} comfortable={comf} g={sc.comfortable_goodness}"
            )
        print()


if __name__ == "__main__":
    _demo()
