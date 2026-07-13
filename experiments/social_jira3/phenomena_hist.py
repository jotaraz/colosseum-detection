#!/usr/bin/env python3
"""Count turn-judge phenomena across a social_jira3 output tree.

Walks a directory for every ``judge_results.json`` (one per run leaf), tallies the
total number of ``present_phenomena`` flags by phenomenon name, and prints a text
table sorted by count. Phenomena defined in the judge taxonomy but never observed
are shown with a count of 0 so the table is always complete.

Usage:
    python -m experiments.social_jira3.phenomena_hist [<root> ...] [--file NAME]

    <root>        a run leaf, a timestamp dir, or any ancestor dir — every
                  judge_results.json underneath it is included. May be repeated.
                  With no root, defaults to every
                  ``outputs/social_jira3_c2p2_*_conflict_quit23_v5_confsweep*``
                  directory (all models, incl. the *_nodecoy variants).
    --file NAME   judge output filename to scan (default: judge_results.json;
                  e.g. pass a repair/alt output name if you used one).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUDGE_PROMPT_PATH = HERE / "JUDGE_TURN_PROMPT.md"
DEFAULT_GLOB = "social_jira3_c2p2_*_conflict_quit23_v5_confsweep*"


def taxonomy() -> list[str]:
    """Phenomenon names in display order, parsed from the judge prompt's
    ``## Phenomenon Taxonomy`` section (bold ``**Name**`` headers on their own line)."""
    try:
        names, in_section = [], False
        for ln in JUDGE_PROMPT_PATH.read_text(encoding="utf-8").splitlines():
            s = ln.strip()
            if s.startswith("## "):
                in_section = s == "## Phenomenon Taxonomy"
                continue
            if in_section:
                m = re.match(r"^\*\*([^*{}]+)\*\*$", s)  # skip template headers like **{agent}...**
                if m:
                    names.append(m.group(1).strip())
        return names
    except Exception:
        return []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="*",
                    help="dir(s) to walk for judge_results.json (leaf, timestamp, or ancestor). "
                         f"Default: outputs/{DEFAULT_GLOB}")
    ap.add_argument("--file", default="judge_results.json", help="judge output filename to scan")
    ap.add_argument("--rollout", action="store_true",
                    help="count rollouts with >=1 turn flagged as X (share is out of all "
                         "rollouts), instead of total turn occurrences")
    args = ap.parse_args()

    if args.root:
        roots = [Path(r).resolve() for r in args.root]
    else:
        roots = sorted((HERE / "outputs").glob(DEFAULT_GLOB))
        if not roots:
            print(f"no dirs match outputs/{DEFAULT_GLOB}", file=sys.stderr)
            return 1
        print(f"# default scan of {len(roots)} dir(s) matching outputs/{DEFAULT_GLOB}:")
        for r in roots:
            print(f"#   {r.name}")

    missing = [r for r in roots if not r.exists()]
    for r in missing:
        print(f"no such path: {r}", file=sys.stderr)
    if missing:
        return 2

    files: list[Path] = []
    for r in roots:
        files.extend(sorted(r.rglob(args.file)) if r.is_dir() else [r])
    if not files:
        print(f"no {args.file} found under the given root(s)", file=sys.stderr)
        return 1

    turn_counts: Counter[str] = Counter()    # total turn occurrences per phenomenon
    rollout_counts: Counter[str] = Counter()  # rollouts with >=1 turn of that phenomenon
    n_runs = 0
    n_turns = 0
    n_turns_with_flag = 0
    n_runs_with_flag = 0
    n_parse_errors = 0
    n_unreadable = 0

    for p in files:
        try:
            d = json.loads(p.read_text())
        except Exception:
            n_unreadable += 1
            continue
        n_runs += 1
        seen_in_run: set[str] = set()
        for t in d.get("turns") or []:
            n_turns += 1
            if t.get("parse_error"):
                n_parse_errors += 1
            phs = t.get("present_phenomena") or []
            if phs:
                n_turns_with_flag += 1
            for ph in phs:
                name = ph.get("phenomenon") or "?"
                turn_counts[name] += 1
                seen_in_run.add(name)
        for name in seen_in_run:
            rollout_counts[name] += 1
        if seen_in_run:
            n_runs_with_flag += 1

    counts = rollout_counts if args.rollout else turn_counts
    # In rollout mode share is out of all rollouts; in turn mode, out of total flags.
    denom = n_runs if args.rollout else sum(turn_counts.values())

    # Order: taxonomy order for known names, then any unknown names by count desc.
    tax = taxonomy()
    ordered = [n for n in tax]
    for name in sorted(counts, key=lambda k: -counts[k]):
        if name not in ordered:
            ordered.append(name)

    width = max((len(n) for n in ordered), default=10)
    print(f"# scanned {n_runs} run(s), {n_turns} turn(s) "
          f"({n_turns_with_flag} turns / {n_runs_with_flag} rollouts with ≥1 flag), "
          f"{sum(turn_counts.values())} total flags")
    if args.rollout:
        print("# --rollout: count = rollouts with ≥1 turn of X; share = out of all rollouts")
    if n_parse_errors:
        print(f"# {n_parse_errors} turn(s) had judge parse_error")
    if n_unreadable:
        print(f"# {n_unreadable} {args.file} file(s) were unreadable and skipped")
    print()
    print(f"{'phenomenon':<{width}}  {'count':>6}  {'share':>7}")
    print(f"{'-' * width}  {'-' * 6}  {'-' * 7}")
    for name in ordered:
        c = counts.get(name, 0)
        share = f"{100 * c / denom:5.1f}%" if denom else "   -  "
        print(f"{name:<{width}}  {c:>6}  {share:>7}")
    print(f"{'-' * width}  {'-' * 6}  {'-' * 7}")
    if args.rollout:
        print(f"{'ROLLOUTS':<{width}}  {n_runs:>6}")
    else:
        print(f"{'TOTAL':<{width}}  {sum(turn_counts.values()):>6}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
