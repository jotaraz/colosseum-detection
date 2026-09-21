"""Sizes and overlaps of the four 5.e.viii seat sets.

pure-none / flagged: ev1 eval-awareness seat lists (outputs/ev1_5e8_seats_*.txt,
one `Seat-model-sN` per line).
set3 / set4: strictfab seats the hand review marks agree / disagree
(outputs/seats_set{3,4}_*.md, one `| model_sN | Seat |` table row per seat).

Seats are normalised to `Seat-model-sN` so the two families compare.
"""
import re
import sys
from pathlib import Path

OUT = Path(__file__).parent / "outputs"
FILES = {
    "pure-none": OUT / "ev1_5e8_seats_pure_none.txt",
    "flagged": OUT / "ev1_5e8_seats_flagged.txt",
    "seat3": OUT / "seats_set3_strictfab_agree.md",
    "seat4": OUT / "seats_set4_strictfab_disagree.md",
}
ROW = re.compile(r"^\|\s*([a-z0-9]+)_s(\d+)\s*\|\s*(Nadia|Priya)\s*\|")


def load(path: Path) -> set[str]:
    seats = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if path.suffix == ".txt":
            if line:
                seats.append(line)
        elif m := ROW.match(line):
            model, s, seat = m.groups()
            seats.append(f"{seat}-{model}-s{s}")
    if len(seats) != len(set(seats)):
        print(f"WARNING: duplicates in {path.name}", file=sys.stderr)
    return set(seats)


def main() -> None:
    S = {k: load(p) for k, p in FILES.items()}
    for k, v in S.items():
        print(f"|{k}| = {len(v)}")
    print()
    pn, fl, s3, s4 = S["pure-none"], S["flagged"], S["seat3"], S["seat4"]
    print(f"pure-none ∩ flagged = {len(pn & fl)}  -> disjoint: {not (pn & fl)}")
    print(f"seat3 ∩ seat4       = {len(s3 & s4)}  -> disjoint: {not (s3 & s4)}")
    print()
    for a, b in [("seat3", "pure-none"), ("seat3", "flagged"),
                 ("seat4", "pure-none"), ("seat4", "flagged")]:
        print(f"|{a} ∩ {b}| = {len(S[a] & S[b])}")
    print()
    ev1 = pn | fl
    for k in ("seat3", "seat4"):
        missing = S[k] - ev1
        if missing:
            print(f"{k} seats not in pure-none ∪ flagged: {sorted(missing)}")
    ev1_runs = {s.split("-", 1)[1] for s in ev1}
    set_runs = {s.split("-", 1)[1] for s in s3 | s4}
    print(f"runs: pure-none ∪ flagged = {len(ev1_runs)}, seat3 ∪ seat4 = {len(set_runs)}, "
          f"set-runs not in ev1: {sorted(set_runs - ev1_runs)}")


if __name__ == "__main__":
    main()
