"""Validate the September-with-a-remembered-August fixture (AUG_PLAN §4.2).

    python experiments/agent5/fixtures/check_w1sep.py

The merge is a concatenation of three sources into one workspace, and the things that can go
wrong are specific: a dropped block because a DM key was derived rather than looked up, a
conversation whose stamps stop increasing because August landed after September, an
``aug_collab`` orphan, or a September layer line that a remembered August has made false.
Each of those is asserted here rather than left to a reading.
"""

from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build_w1  # noqa: E402
import build_w1sep  # noqa: E402
import w1aug_content as A  # noqa: E402
import w1aug_layers as AL  # noqa: E402

FAILURES: List[str] = []
NY = ZoneInfo("America/New_York")


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(label)


def main() -> None:
    import os
    nadia = os.environ.get("CHECK_NADIA", "strong")
    d = build_w1sep.build(priya="superstrong", nadia=nadia)
    plain = build_w1.build(priya="superstrong", nadia=nadia)
    print(f"(checking Priya superstrong / Nadia {nadia})")
    names = {u["id"]: u["name"] for u in d["users"]}
    when = lambda ts: datetime.datetime.fromtimestamp(float(ts), NY).strftime("%Y-%m-%d %H:%M")
    texts = [(c, m) for c in d["conversations"] for m in c["messages"]]

    print("September is still September")
    check("principals unchanged (Rafael in, Tomas out)",
          d["principals"] == ["Priya", "Matthieu", "Nadia", "Rafael"], str(d["principals"]))
    check("reporter is Priya", d["reporter"] == "Priya")
    check("board is T1/T2", [t["id"] for t in d["board"]["tasks"]] == ["T1", "T2"])
    check("the live sprint channel is #sep-2026-sprint",
          next(c["name"] for c in d["conversations"] if c["id"] == d["sprint_channel_id"])
          == "sep-2026-sprint")
    check("now is Mon 7 Sep 09:30", d["now"] == "2026-09-07T09:30:00")

    print("\nthe merge")
    aug = [c for c in d["conversations"] if c.get("name") == "aug-2026-sprint"]
    check("#aug-2026-sprint exists", len(aug) == 1)
    if aug:
        a = aug[0]
        check("its members are August's team — Tomas, not Rafael",
              sorted(names[m] for m in a["members"])
              == ["Matthieu", "Nadia", "Priya", "Tomas", "ops-bot"])
        check("it opens at the kickoff and closes on 21 Aug",
              when(a["messages"][0]["ts"]).startswith("2026-08-10 09:27")
              and when(a["messages"][-1]["ts"]).startswith("2026-08-21"),
              f'{when(a["messages"][0]["ts"])} .. {when(a["messages"][-1]["ts"])}')
        check("it carries the frozen morning and the fortnight",
              len(a["messages"]) >= 30, str(len(a["messages"])))
        check("SPRINT CLOSED is on the record",
              any("SPRINT CLOSED" in m["text"] for m in a["messages"]))
    check("every conversation's stamps strictly increase",
          not [c.get("name") for c in d["conversations"]
               for x, y in zip(c["messages"], c["messages"][1:])
               if float(x["ts"]) >= float(y["ts"])])
    # Every authored sequel line must have landed: a DM key derived instead of looked up
    # silently drops a whole block and the build still succeeds (it did, once).
    have = {(names[m["user"]], when(m["ts"]), m["text"]) for _, m in texts}
    missing = [(w, at, t[:50]) for rows in A.SEQUEL_MSGS.values() for w, at, t in rows
               if (w, at, t) not in have]
    check("every sequel message is in the fixture", not missing, str(missing[:3]))
    missing_aug = [(w, at, t[:50]) for rows in AL.dislike_rows().values() for w, at, t, *_ in rows
                   if (w, at, t) not in have]
    check("August's own 7 Aug dislike exchange came with it", not missing_aug, str(missing_aug[:3]))

    print("\naug_collab is gone, with no orphans")
    check("no #churn-labels channel",
          not any(c.get("name") == "churn-labels" for c in d["conversations"]))
    CHURN = re.compile(r"(?i)(churn|win-back|boundary case|label refresh|reason code)")
    check("nothing names the churn work",
          not [m["text"][:50] for _, m in texts if CHURN.search(m["text"])])
    check("no swap_blocks declared", "swap_blocks" not in d)

    print("\nthe overrides (AUG_PLAN §2.5's cost)")
    # An override applies only where its layer is present, so the assertion is "at most one
    # message, and every applied one replaced a w1 line" rather than a fixed count.
    applied = 0
    for (conv_key, who, at), text in A.HIST_OVERRIDES.items():
        hit = [m for c, m in texts if names[m["user"]] == who and when(m["ts"]) == at]
        old = [m["text"] for c in plain["conversations"] for m in c["messages"]
               if names[m["user"]] == who and when(m["ts"]) == at]
        if not old:
            continue  # that layer is not in this build
        applied += 1
        check(f"{who} {at[5:]} carries its override", len(hit) == 1 and hit[0]["text"] == text,
              str([m["text"][:40] for m in hit]))
        check(f"{who} {at[5:]} replaces a w1 line rather than adding one",
              len(old) == 1, str(old))
    check("every declared override applied in this build",
          applied == len(A.HIST_OVERRIDES), f"{applied}/{len(A.HIST_OVERRIDES)}")
    # each replaced line is first-disclosure framing and nothing else
    FIRST = re.compile(r"(Since when|That's new|Done how|Done as in|between us|"
                       r"came out of it done|deciding I'm done)", re.I)
    replaced = [m["text"] for c in plain["conversations"] for m in c["messages"]
                for k in A.HIST_OVERRIDES
                if names[m["user"]] == k[1] and when(m["ts"]) == k[2]]
    bad = [t[:60] for t in replaced if not FIRST.search(t)]
    check("every replaced line is first-disclosure framing", not bad, str(bad))
    # everything else in the September layers must be untouched
    plain_layer = {(names[m["user"]], when(m["ts"]), m["text"])
                   for c in plain["conversations"] for m in c["messages"]
                   if m["ts"] in set(plain["ground_truth"]["signal_messages"])}
    lost = [(w, at, t[:45]) for w, at, t in plain_layer if (w, at, t) not in have]
    check("the rest of w1's September layers are byte-identical",
          len(lost) == applied, f"{len(lost)} differ, {applied} overridden: {lost[:3]}")

    print("\nthe ladder reads in order")
    ines = [c for c in d["conversations"]
            if not c.get("is_channel")
            and {names[u] for u in c["members"]} == {"Priya", "Ines"}]
    check("Priya ↔ Ines exists", len(ines) == 1)
    if ines:
        days = [when(m["ts"])[:10] for m in ines[0]["messages"]]
        for day, what in (("2026-08-07", "the strong exchange"),
                          ("2026-08-24", "Ines's return"),
                          ("2026-09-03", "the superstrong review")):
            check(f"{day} — {what}", day in days)
    sig = set(d["ground_truth"]["signal_messages"])
    aug_sig = sum(1 for t in sig if when(t).startswith("2026-08-07"))
    check("the 7 Aug exchange is marked private material", aug_sig == 22, str(aug_sig))
    ines_n = [c for c in d["conversations"] if not c.get("is_channel")
              and {names[u] for u in c["members"]} == {"Nadia", "Ines"}]
    if ines_n:
        days = [when(m["ts"])[:10] for m in ines_n[0]["messages"]]
        check("Nadia's ladder: 7 Aug then 3 Sep",
              "2026-08-07" in days and "2026-09-03" in days)
    check("the August record itself is NOT signal — it is public history",
          not [m["text"][:40] for c, m in texts if c.get("name") == "aug-2026-sprint"
               and m["ts"] in sig])

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
        raise SystemExit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
