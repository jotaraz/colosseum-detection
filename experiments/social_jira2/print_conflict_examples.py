"""Print the full ground truth of conflict scenarios so you can reason through them by hand.

Usage:
    python3 experiments/social_jira2/print_conflict_examples.py 11 22 99 37
    python3 experiments/social_jira2/print_conflict_examples.py          # defaults to 11 22 99 37

Names are display placeholders (real runs draw names from a seed+1337 rng); roles, seniority,
goodness, and feelings are exactly what scenario.generate_scenario produces.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from experiments.social_jira2.scenario import (  # noqa: E402
    generate_scenario, iter_matchings, pair_key, required_phrase,
)

NAME_POOL = (
    "Mara", "Theo", "Priya", "Diego", "Noor", "Sven", "Lena", "Ravi",
    "Yuki", "Omar", "Greta", "Ivo", "Tariq", "Esme", "Bo", "Aria",
)


def _fmt_pair(p):
    return f"{p[0]} & {p[1]}"


def print_seed(seed: int) -> None:
    names = list(NAME_POOL[(seed * 3) % len(NAME_POOL):][:4]) or list(NAME_POOL[:4])
    if len(names) < 4:
        names = list(NAME_POOL[:4])
    sc = generate_scenario(seed=seed, employees=names, num_tasks=2, scenario_type="conflict")
    nm = sc.names

    print("#" * 72)
    print(f"SEED {seed}   (G*={sc.optimal_goodness:.0f}, comfortable={sc.comfortable_goodness:.0f})")
    print("#" * 72)
    print("\nROSTER (profession, seniority):")
    for e in sc.employees:
        print(f"   {e.name:7s}  {e.profession:18s}  {e.seniority}")

    print("\nTASKS:")
    for t in sc.tasks:
        print(f"   {t.id}: {t.title:34s} requires {required_phrase(t.required)}  {tuple(t.required)}")

    print("\nGOODNESS  [coverage*10 + correctly-slotted-seniors*1]  (all 6 pairs x 2 tasks):")
    print("   pair".ljust(22) + "".join(f"{t.id:>8s}" for t in sc.tasks))
    for a in nm:
        for b in nm:
            if a < b:
                pk = pair_key(a, b)
                print(f"   {_fmt_pair(pk):18s}" + "".join(f"{sc.goodness[t.id][pk]:8.0f}" for t in sc.tasks))

    print("\nFEELINGS (row dislikes column; -1 = dislike, 0 = neutral):")
    print("   " + "from\\to".ljust(9) + "".join(f"{n[:6]:>8s}" for n in nm))
    for a in nm:
        cells = "".join(
            ("     -1" if (a != b and sc.feelings[a][b] == -1) else ("      ." if a != b else "      -"))
            for b in nm
        )
        print(f"   {a[:8]:8s}" + cells)

    print("\nALL MATCHINGS (each task -> a disjoint pair):")
    rows = []
    for m in iter_matchings(nm, sc.task_ids):
        g = sc.matching_goodness(m)
        aversive = any(sc.pair_is_aversive(p) for p in m.values())
        rows.append((g, aversive, m))
    rows.sort(key=lambda r: -r[0])
    for g, aversive, m in rows:
        tag = []
        if abs(g - sc.optimal_goodness) < 1e-9:
            tag.append("OPTIMAL G*")
        if not aversive:
            tag.append("conflict-free")
        if sc.comfortable_matching and all(
            pair_key(*m[t]) == pair_key(*sc.comfortable_matching[t]) for t in m
        ):
            tag.append("<-COMFORTABLE")
        desc = "  ".join(f"{t}:{_fmt_pair(m[t])}" for t in sc.task_ids)
        astr = "AVERSIVE" if aversive else "ok      "
        print(f"   g={g:5.0f}  {astr}  {desc:48s} {'  '.join(tag)}")

    m = sc.meta
    print(f"\nmeta: generator={m['conflict_generator']} source_seed={m['conflict_source_seed']} "
          f"fell_back={m['conflict_fell_back']} feelings_fallback={m['feelings_fallback']}\n")


def main() -> None:
    seeds = [int(s) for s in sys.argv[1:]] or [11, 22, 99, 37]
    for seed in seeds:
        print_seed(seed)


if __name__ == "__main__":
    main()
