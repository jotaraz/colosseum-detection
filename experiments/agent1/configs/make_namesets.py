from __future__ import annotations

"""Derive a name-set config from a committed one, so the two differ ONLY in the cast.

A name set (``build_workspace.NAME_MAPS``) swaps the four principals for a differently
gendered cast and repairs the pronouns that follow. It is not a fixture version: `version`
and `note` are untouched and `build()` is not re-run. But a run also needs a config, and a
config carries three things the swap has to reach — the fixture path, `start_with`, and the
inline ask text, which names the mandate's recipient, the rival, and the avoided colleague.

Deriving rather than writing means the arm cannot drift: the source is the config that
already ran, so `v15nb askG` differs from `v15 askG` by the cast and nothing else. Writing
these by hand is how a cell ends up differing by a stray comma nobody notices.

    python -m experiments.agent1.configs.make_namesets --fixture v15 --arm askA askG \
        --name-set b c d

Name-set fixtures must exist first::

    python experiments/agent1/fixtures/build_workspace.py --variant v15 --rename \
        --name-set b experiments/agent1/fixtures/aug2026_v15_renamed_b.json
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, Tuple

try:
    from experiments.agent1.fixtures.build_workspace import DEFAULT_NAME_SET, NAME_MAPS
except ImportError:  # run as a plain script rather than `python -m`
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from experiments.agent1.fixtures.build_workspace import DEFAULT_NAME_SET, NAME_MAPS

HERE = Path(__file__).resolve().parent

#: The fixture token a name set gets in config and output names: `v15` -> `v15nb`. NOT `v15b`,
#: which would read as a content version alongside v15/v16 and is exactly the confusion this
#: is trying to avoid — a name set changes nobody's words.
def token(fixture: str, name_set: str) -> str:
    return f"{fixture}n{name_set}"


def cast_map(name_set: str) -> Dict[str, str]:
    """Set `a`'s names -> this set's, i.e. the substitution a committed config needs.

    Everything downstream reads set `a` (Priya/Marcus/Nadia/Tomas), so the map is composed
    through the placeholder names rather than stated twice. Entries that do not move are
    dropped, which keeps the regex to the names that actually change.
    """
    base, target = NAME_MAPS[DEFAULT_NAME_SET], NAME_MAPS[name_set]
    return {base[k]: target[k] for k in base if base[k] != target[k]}


#: Pronouns in the ask text, applied AFTER the names, same contract as the fixture's
#: `PRONOUN_EDITS`: exact sentences, asserted to fire, name-free so one table serves every
#: set. Only askF/G/H carry one — "make sure that I don't have to work with him", where `him`
#: is the avoided colleague — and it moves only when that colleague does.
_MASCULINE = re.compile(r"\b(he|him|his|himself)\b")

_AVOIDED_HE_TO_SHE: Tuple[Tuple[str, str], ...] = (
    ("to work with him.", "to work with her."),
)


def pronoun_edits(name_set: str) -> Tuple[Tuple[str, str], ...]:
    """The avoided colleague is Bob. If this set leaves Bob alone, so do the pronouns."""
    base, target = NAME_MAPS[DEFAULT_NAME_SET], NAME_MAPS[name_set]
    return _AVOIDED_HE_TO_SHE if base["Bob"] != target["Bob"] else ()


HEADER = """# --- NAME SET {ns} --------------------------------------------------------------------
# Derived from {src} by make_namesets.py. The world is {ver}'s, unchanged —
# same `version`, same `note`, same 301 messages — with the four principals renamed and the
# pronouns that follow them repaired:
#
#     {cast}
#
# So this cell against the {ver} one isolates the cast and nothing else. Edit the source
# config or build_workspace.NAME_MAPS, not this file.
"""


def derive(text: str, fixture: str, name_set: str, src_name: str) -> str:
    names = cast_map(name_set)
    tok = token(fixture, name_set)

    # Paths first: they carry no names, and doing them before the substitution keeps the
    # regex below from having to care whether a fixture filename ever grows one.
    text = text.replace(
        f"fixtures/aug2026_{fixture}_renamed.json",
        f"fixtures/aug2026_{fixture}_renamed_{name_set}.json")
    text = text.replace(f"outputs/{fixture}/", f"outputs/{tok}/")

    if names:
        pattern = re.compile(r"\b(" + "|".join(names) + r")\b")
        text = pattern.sub(lambda m: names[m.group(1)], text)

    # Opportunistic, then verified: askA carries no pronoun at all and askG carries exactly
    # one, so a per-arm table of what "must" fire would be a table of arms. The invariant is
    # cheaper and catches more — once this set has moved the avoided colleague, no masculine
    # pronoun may survive anywhere in the file. A reworded ask that grows a second one fails
    # here rather than shipping a config in which Martha is "he".
    for old, new in pronoun_edits(name_set):
        text = text.replace(old, new)
    if pronoun_edits(name_set) and (stray := _MASCULINE.findall(text)):
        raise AssertionError(
            f"{src_name}: name set {name_set!r} makes the avoided colleague female, but "
            f"{stray} survive(s) the substitution. Add the sentence to _AVOIDED_HE_TO_SHE.")

    cast = "  ".join(f"{k} -> {v}" for k, v in cast_map(name_set).items())
    return HEADER.format(ns=name_set, src=src_name, ver=fixture, cast=cast) + text


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Derive name-set configs from committed ones.")
    parser.add_argument("--fixture", default="v15")
    parser.add_argument("--arm", nargs="+", required=True)
    parser.add_argument("--name-set", nargs="+", required=True,
                        choices=sorted(s for s in NAME_MAPS if s != DEFAULT_NAME_SET))
    parser.add_argument("--model", nargs="+", default=["deepseek", "kimi", "glm"])
    parser.add_argument("--confidentiality", nargs="+", default=["inf"])
    parser.add_argument("--force", action="store_true", help="overwrite existing files")
    args = parser.parse_args()

    for name_set in args.name_set:
        fixture = HERE.parents[0] / "fixtures" / \
            f"aug2026_{args.fixture}_renamed_{name_set}.json"
        if not fixture.exists():
            print(f"  skip set {name_set} — build {fixture.name} first")
            continue
        for arm in args.arm:
            for conf in args.confidentiality:
                for model in args.model:
                    src = HERE / f"agent1_{args.fixture}_{conf}_{arm}_{model}.yaml"
                    if not src.exists():
                        print(f"  skip {src.name} — no source config for that cell")
                        continue
                    out = HERE / (f"agent1_{token(args.fixture, name_set)}_{conf}_"
                                  f"{arm}_{model}.yaml")
                    if out.exists() and not args.force:
                        print(f"  skip {out.name} — exists (use --force)")
                        continue
                    out.write_text(
                        derive(src.read_text(), args.fixture, name_set, src.name),
                        encoding="utf-8")
                    print(f"  wrote {out.name}")


if __name__ == "__main__":
    main()
