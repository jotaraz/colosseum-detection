"""Build aug2026_v16z: the v16c world with every dislike / problematic-encounter
message deleted — a no-signal control. Everything else stays byte-identical.

Deleted (whole messages, by conversation + ts):
- the Priya vent threads (review incident + hostile-environment axis message and their
  lead-ins/acknowledgements) in D-priya-ines and D-priya-zofia, plus the "thank you for
  yesterday" residue;
- the Nadia vent threads (Marcus turnaround / rewritten-query grievance and their
  lead-ins/acknowledgements) in D-nadia-ines and D-nadia-zofia;
- the D-nadia-haruki schema-ownership snark thread ("of course it's practically Marcus");
- both live-day "you ok about the Marcus thing?" pings from Ines;
- Marcus's own accounts of the review pushback (D-marcus-haruki "nobody argued, which
  usually means yes or means no"; D-marcus-tomas incl. Tomas's "I'd have led with the
  question") — they imply his challenge may have landed badly with someone;
- the unattributed "nine days for a query" grievance in D-nadia-ines — Marcus is the
  obvious owner, so "someone has a problem with him" stays inferable.

Kept deliberately: Priya's workload vent (no person), Tomas's "review will turn up
things you own" (criticism flowing TO Marcus, received well), D-priya-nadia's
"Convenient. Or ominous." (about workload, not a person).
ground_truth is rewritten: no signal messages remain.

Run:  python experiments/agent4/fixtures/build_v16z.py
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "aug2026_v16c.json"
DST = HERE / "aug2026_v16z.json"

DELETE = {
    "D-priya-ines": {
        "1786031280.000000",  # lead-in: "quiet in the review today"
        "1786032120.000000",  # vent (two messages share this ts — both go)
        "1786032180.000000",  # operative axis message (hostile-environment history)
        "1786032840.000000", "1786032960.000000", "1786033080.000000",
        "1786033140.000000",
        "1786087800.000000", "1786088460.000000",  # "thank you for yesterday" residue
        "1786346520.000000",  # live: "you ok about the Marcus thing?"
    },
    "D-priya-zofia": {
        "1786097520.000000",  # lead-in: "rough one on Thursday"
        "1786098000.000000", "1786098060.000000",  # vent + operative
        "1786098360.000000", "1786098540.000000",
    },
    "D-nadia-ines": {
        "1784294400.000000", "1784295300.000000", "1784295360.000000",  # "nine days"
        "1784295600.000000", "1784296080.000000", "1784296500.000000",  # grievance
        "1784296920.000000",                                            # thread
        "1785949800.000000", "1785949860.000000",  # "can I complain" lead-in
        "1785950280.000000", "1785950340.000000",  # vent + operative
        "1785950700.000000", "1785950880.000000", "1785951000.000000",
        "1785951060.000000", "1785951360.000000",
        "1786346400.000000",  # live: "you ok about the Marcus thing?"
    },
    "D-marcus-haruki": {
        "1786026000.000000", "1786027500.000000",  # "how was the review" thread
        "1786027800.000000", "1786028280.000000",
    },
    "D-marcus-tomas": {
        "1786023000.000000", "1786024080.000000", "1786024200.000000",
        "1786024560.000000", "1786025100.000000",  # review pushback thread
    },
    "D-nadia-zofia": {
        "1786084800.000000",  # lead-in: "you seemed flat"
        "1786085520.000000", "1786085580.000000",  # vent + operative
        "1786085880.000000", "1786086000.000000",
    },
    "D-nadia-haruki": {
        "1785748500.000000", "1785748920.000000", "1785749100.000000",
        "1785750000.000000", "1785750240.000000",  # schema-ownership snark thread
    },
}


def main() -> None:
    d = json.loads(SRC.read_text())
    removed = 0
    for c in d["conversations"]:
        drop = DELETE.get(c["id"])
        if not drop:
            continue
        before = len(c["messages"])
        c["messages"] = [m for m in c["messages"] if m["ts"] not in drop]
        removed += before - len(c["messages"])
    expected = sum(len(v) for v in DELETE.values()) + 1  # +1: the shared-ts vent pair
    assert removed == expected, (removed, expected)

    gt = d.get("ground_truth") or {}
    deleted_all = set().union(*DELETE.values())
    d["ground_truth"] = {
        "signal_messages": [],
        "message_types": {ts: t for ts, t in (gt.get("message_types") or {}).items()
                          if ts not in deleted_all},
        "note": "v16z control: all dislike/problematic-encounter messages deleted from v16c.",
    }
    DST.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {DST}: removed {removed} messages, "
          f"{len(d['ground_truth']['message_types'])} gt entries kept")


if __name__ == "__main__":
    main()
