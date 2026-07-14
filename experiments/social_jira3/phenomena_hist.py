#!/usr/bin/env python3
"""Count turn-judge phenomena across a social_jira3 output tree.

Walks a directory for every ``judge_results.json`` (one per run leaf), tallies the
total number of ``present_phenomena`` flags by phenomenon name, and prints a text
table sorted by count. Phenomena defined in the judge taxonomy but never observed
are shown with a count of 0 so the table is always complete.

Usage:
    python -m experiments.social_jira3.phenomena_hist [<root> ...] [--file NAME]
        [--model] [--decoy] [--hint] [--seed] [--sample] [--conf] [--quit]

    <root>        a run leaf, a timestamp dir, or any ancestor dir — every
                  judge_results.json underneath it is included. May be repeated.
                  With no root, defaults to every
                  ``outputs/social_jira3_c2p2_*_conflict_quit23_v5_confsweep*``
                  directory (all models, incl. the *_nodecoy variants).
    --file NAME   judge output filename to scan (default: judge_results.json;
                  e.g. pass a repair/alt output name if you used one).
    --include-incomplete
                  keep rollouts whose sibling metrics.json status != "completed".
                  By default such runs (crashed/truncated) are dropped from BOTH the
                  numerator and denominator, so shares are a rate over fully-measured
                  rollouts. Runs with no readable metrics.json are always kept.

    Axis flags (--model/--decoy/--hint/--seed/--sample/--conf/--quit) do double duty:
      * bare (e.g. --hint)          -> SPLIT: one histogram per distinct value.
      * with a value (--hint=none)  -> FILTER: keep only rollouts with that value.
    Passed bare, several flags split by their combination; these 7 axes fully identify
    a rollout, so all of them bare yields one histogram per rollout. Filters and splits
    compose (e.g. --conf=self --model splits the conf=self subset by model). Values:
      model  qwen | gpt-oss | glm            decoy  on | off
      hint   none | small | big | noconstraint    seed  1 | 2 (any int)
      sample 0 | 1 | 2 (any int)            quit  mild | strong | quit | quit2 | quit3
      conf   none | audience | duty | self | stakes | leverage | integrity | consent
    Axis values are read from the run leaf directory name, so they work for any --file.
    --rollout is orthogonal (it selects the count mode, not a split axis).
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

# Level-2 confirmation files (judge.py --level2), folded in as their own phenomena: each
# confirmed turn (present==True) counts once toward the display name, independent of the
# L1 phenomenon it confirms. Maps the per-run L2 filename -> the phenomenon display name.
L2_PHENOMENA = {
    "judge_l2_fabrication_executed.json": "L2 Fabrication (executed)",
}

# Sweep axes that fully identify a rollout, in canonical display order. Each maps to
# a --<flag>: bare splits by that axis, `--flag=value` filters to that value.
SPLIT_KEYS = ["model", "decoy", "hint", "seed", "sample", "conf", "quit"]
_UNKNOWN = "?"
_SPLIT = object()  # sentinel: flag passed bare (split), as opposed to a filter value

# Allowed filter values per axis (None = accept any, e.g. numeric seed/sample). Bad
# values are rejected with a helpful message. Kept in sync with prompts.py / run.py.
AXIS_VALUES: dict[str, tuple[str, ...] | None] = {
    "model": ("qwen", "gpt-oss", "glm"),
    "decoy": ("on", "off"),
    "hint": ("none", "small", "big", "noconstraint"),
    "seed": None,
    "sample": None,
    "conf": ("none", "audience", "duty", "self", "stakes", "leverage", "integrity", "consent"),
    "quit": ("mild", "strong", "quit", "quit2", "quit3"),
}


def _model_alias(s: str) -> str:
    """Collapse a model_label (or user input) to the short axis value qwen|gpt-oss|glm."""
    s = s.strip().lower()
    if "qwen" in s:
        return "qwen"
    if "gpt" in s and "oss" in s:
        return "gpt-oss"
    if "glm" in s:
        return "glm"
    return s


def parse_params(path: Path) -> dict[str, str]:
    """Recover the sweep parameters from a run leaf directory name.

    Leaf format (experiments/social_jira3/run.py):
        {model_label}__{channel}-{strength}-conf{conf}-hint{hint}-{audience}-dec{decoys}
        __{scenario}__{setup}__{personality}__{topology}__n{n}__t{t}__seed{seed}__s{sample}

    Returns a value for every SPLIT_KEYS entry, using "?" for anything unparseable.
    Reading from the path (not the JSON body) keeps splitting independent of --file.
    """
    out = {k: _UNKNOWN for k in SPLIT_KEYS}
    leaf = path.parent.name if path.is_file() or path.suffix else path.name
    parts = leaf.split("__")
    if parts and parts[0]:
        out["model"] = _model_alias(parts[0])  # vllm-glm-4.7-flash -> glm
    if len(parts) >= 2:
        cell = parts[1].split("-")  # channel-strength-conf<X>-hint<Y>-audience-dec<Z>
        if len(cell) >= 2:
            out["quit"] = cell[1]   # dislike_strength: mild|strong|quit|quit2|quit3
        for tok in cell:
            if tok.startswith("conf"):
                out["conf"] = tok[len("conf"):]
            elif tok.startswith("hint"):
                out["hint"] = tok[len("hint"):]
            elif tok.startswith("dec"):
                out["decoy"] = tok[len("dec"):]  # on|off
    for tok in parts:
        if re.fullmatch(r"seed\d+", tok):
            out["seed"] = tok[len("seed"):]
        elif re.fullmatch(r"s\d+", tok):
            out["sample"] = tok[len("s"):]
    return out


def run_status(judge_path: Path) -> str | None:
    """Read the run status from the metrics.json sitting next to a judge_results.json.
    Returns the status string (e.g. 'completed'/'incomplete'), or None if unavailable."""
    try:
        d = json.loads((judge_path.parent / "metrics.json").read_text())
        return d.get("status")
    except Exception:
        return None


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
    ap.add_argument("--include-incomplete", action="store_true",
                    help="also count rollouts whose sibling metrics.json status != 'completed' "
                         "(crashed/truncated runs); by default they are dropped so shares are a "
                         "rate over fully-measured rollouts")
    for k in SPLIT_KEYS:
        allowed = AXIS_VALUES[k]
        vals = "|".join(allowed) if allowed else "<any>"
        ap.add_argument(f"--{k}", nargs="?", const=_SPLIT, default=None, metavar=vals,
                        help=f"bare: split by {k}; --{k}=VALUE: keep only that {k} ({vals})")
    args = ap.parse_args()

    # A bare axis flag (const _SPLIT) is a split key; an axis flag with a value filters.
    split_keys: list[str] = []
    filters: dict[str, str] = {}
    for k in SPLIT_KEYS:
        v = getattr(args, k)
        if v is None:
            continue
        if v is _SPLIT:
            split_keys.append(k)
            continue
        v = _model_alias(v) if k == "model" else str(v).strip().lower()
        allowed = AXIS_VALUES[k]
        if allowed and v not in allowed:
            ap.error(f"--{k}={v!r} is not a valid {k}; choose from {', '.join(allowed)}")
        filters[k] = v

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

    # One bucket per group. With no split flags there is a single group (key ()).
    buckets: dict[tuple[str, ...], dict] = {}
    n_unreadable = 0
    n_filtered_out = 0
    n_incomplete = 0

    def _bucket(key: tuple[str, ...]) -> dict:
        return buckets.setdefault(key, {
            "turn_counts": Counter(),      # total turn occurrences per phenomenon
            "rollout_counts": Counter(),   # rollouts with >=1 turn of that phenomenon
            "n_runs": 0, "n_turns": 0,
            "n_turns_with_flag": 0, "n_runs_with_flag": 0, "n_parse_errors": 0,
        })

    for p in files:
        try:
            d = json.loads(p.read_text())
        except Exception:
            n_unreadable += 1
            continue
        params = parse_params(p)
        if any(params.get(k) != val for k, val in filters.items()):
            n_filtered_out += 1
            continue
        # Gate on completion: a crashed/truncated run is not a valid observation, so it
        # is dropped from both numerator and denominator unless --include-incomplete.
        if not args.include_incomplete and run_status(p) not in (None, "completed"):
            n_incomplete += 1
            continue
        b = _bucket(tuple(params[k] for k in split_keys))
        b["n_runs"] += 1
        seen_in_run: set[str] = set()
        for t in d.get("turns") or []:
            b["n_turns"] += 1
            if t.get("parse_error"):
                b["n_parse_errors"] += 1
            phs = t.get("present_phenomena") or []
            if phs:
                b["n_turns_with_flag"] += 1
            for ph in phs:
                name = ph.get("phenomenon") or "?"
                b["turn_counts"][name] += 1
                seen_in_run.add(name)
        # Fold in level-2 confirmation passes (sibling files) as independent phenomena: one
        # count per confirmed turn (present==True), counted like any other phenomenon.
        for l2_file, l2_phenomenon in L2_PHENOMENA.items():
            l2p = p.parent / l2_file
            if not l2p.is_file():
                continue
            try:
                l2d = json.loads(l2p.read_text())
            except Exception:
                continue
            for t in l2d.get("turns") or []:
                if t.get("present"):
                    b["turn_counts"][l2_phenomenon] += 1
                    seen_in_run.add(l2_phenomenon)
        for name in seen_in_run:
            b["rollout_counts"][name] += 1
        if seen_in_run:
            b["n_runs_with_flag"] += 1

    tax = taxonomy()
    # L2 phenomena aren't headers in the judge prompt; slot each after its L1 sibling (or
    # append) so it always shows in the table, even at count 0.
    for l2_phenomenon in L2_PHENOMENA.values():
        if l2_phenomenon in tax:
            continue
        base = l2_phenomenon[3:] if l2_phenomenon.startswith("L2 ") else None
        if base and base in tax:
            tax.insert(tax.index(base) + 1, l2_phenomenon)
        else:
            tax.append(l2_phenomenon)
    if args.rollout:
        print("# --rollout: count = rollouts with ≥1 turn of X; share = out of all rollouts")
    if n_incomplete:
        print(f"# dropped {n_incomplete} rollout(s) with metrics.json status != 'completed' "
              f"(pass --include-incomplete to keep them)")
    if filters:
        matched = sum(b["n_runs"] for b in buckets.values())
        print(f"# filter {', '.join(f'{k}={v}' for k, v in filters.items())} -> "
              f"{matched} of {matched + n_filtered_out} run(s) kept")
    if split_keys:
        print(f"# split by {', '.join(split_keys)} -> {len(buckets)} histogram(s)")
    if not buckets:
        print("# no runs matched", file=sys.stderr)
        return 1
    if n_unreadable:
        print(f"# {n_unreadable} {args.file} file(s) were unreadable and skipped")

    # Sort groups by their key (numeric-aware for seed/sample).
    def _sort_key(key: tuple[str, ...]):
        return tuple(v.zfill(4) if v.isdigit() else v for v in key)

    for key in sorted(buckets, key=_sort_key):
        _print_bucket(buckets[key], split_keys, key, tax, args.rollout)
    return 0


def _print_bucket(b: dict, split_keys: list[str], key: tuple[str, ...],
                  tax: list[str], rollout: bool) -> None:
    turn_counts, rollout_counts = b["turn_counts"], b["rollout_counts"]
    counts = rollout_counts if rollout else turn_counts
    total_flags = sum(turn_counts.values())
    # In rollout mode share is out of all rollouts; in turn mode, out of total flags.
    denom = b["n_runs"] if rollout else total_flags

    # Order: taxonomy order for known names, then any unknown names by count desc.
    ordered = list(tax)
    for name in sorted(counts, key=lambda k: -counts[k]):
        if name not in ordered:
            ordered.append(name)
    width = max((len(n) for n in ordered), default=10)

    print()
    if split_keys:
        label = "  ".join(f"{k}={v}" for k, v in zip(split_keys, key))
        print(f"## {label}")
    print(f"# scanned {b['n_runs']} run(s), {b['n_turns']} turn(s) "
          f"({b['n_turns_with_flag']} turns / {b['n_runs_with_flag']} rollouts with ≥1 flag), "
          f"{total_flags} total flags")
    if b["n_parse_errors"]:
        print(f"# {b['n_parse_errors']} turn(s) had judge parse_error")
    print(f"{'phenomenon':<{width}}  {'count':>6}  {'share':>7}")
    print(f"{'-' * width}  {'-' * 6}  {'-' * 7}")
    for name in ordered:
        c = counts.get(name, 0)
        share = f"{100 * c / denom:5.1f}%" if denom else "   -  "
        print(f"{name:<{width}}  {c:>6}  {share:>7}")
    print(f"{'-' * width}  {'-' * 6}  {'-' * 7}")
    if rollout:
        print(f"{'ROLLOUTS':<{width}}  {b['n_runs']:>6}")
    else:
        print(f"{'TOTAL':<{width}}  {total_flags:>6}")


if __name__ == "__main__":
    raise SystemExit(main())
