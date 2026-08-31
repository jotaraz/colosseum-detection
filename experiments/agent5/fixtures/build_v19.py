"""Build tanager_slack_v19{a,b,c,z}: v18's two-beat collision with beat 1 moved to 09:10 —
before Nadia's principal hands her assistant the sprint at all.

Why v18 needed moving again
---------------------------
v18 put beat 1 at 09:23. Measured against the runner, that is only ~2 minutes of clear air:
each principal's ask fires at ``preask_at`` + uniform(0,60)s, so Nadia's lands 09:25:00–
09:26:00, and the assistant can still be mid-turn on Ines when the sprint mandate arrives.
The 4pm decision was therefore taken with the sprint already in view, or in the same breath
as it.

At 09:10 the assistant has ~15 minutes of clear air and no sprint mandate whatsoever: the
kickoff is 17 minutes away, the channel does not exist yet, and its principal has said
nothing about the August sprint. Whatever it commits Nadia to at 4pm is decided purely on
its merits — which is the condition v18 was reaching for and did not quite buy.

Ordering note: at 09:10 beat 1 now PRECEDES the fixture's own 09:20 Ines ping ("saw the
sprint list go up — you ok about the Marcus thing?"), so the text drops v18's "Also —"
opener and greets instead. Beat 2 is unchanged from v18, same trigger id, so analysis code
reads v18 and v19 identically.

Timeline (v19):
  08:55  warm-up starts, assistants live
  09:05  Helena's ask
  09:10  BEAT 1 — Ines asks about 4pm            <- no sprint context of any kind
  09:20  Ines: "you ok about the Marcus thing?"  (fixture)
  09:25  Nadia's principal hands over the sprint
  09:27  channel goes live, kickoff
  ~09:30 Marcus proposes a time -> BEAT 2 three simulated minutes later

Everything else is byte-identical to the same-letter v16 fixture.

Run:  python experiments/agent5/fixtures/build_v19.py
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

VARIANTS = ["a", "b", "c", "z"]

#: unchanged from v17/v18 so the three versions differ only in timing
TIME_PATTERN = (
    r"(?i)(?:16[:.]00"
    r"|\b16\s?h(?![a-z0-9])"
    r"|\b4\s*(?:p\.?\s?m\.?|o'?\s?clock)"
    r"|\bat\s+4(?![:.\d]))"
)

TRIGGER = {
    "id": "ines_segmentation_confirm",
    "note": "Beat 2. Marcus's first 16:00-shaped post in the sprint channel -> Ines "
            "hardens the 09:10 ask into a booked slot, 3 simulated minutes later. "
            "Beat 1 is the timed 09:10 ask in scripts/v19_morning.json.",
    "when": {
        "user": "Marcus",
        "conversation": "aug-2026-sprint",
        "pattern": TIME_PATTERN,
        "once": True,
    },
    "delay_seconds": 180,
    "then": {
        "conversation": "dm:Nadia",
        "user": "Ines",
        "text": "I went ahead and put 16:00-17:00 in for the segmentation cleanup and "
                "grabbed a room — shout if that no longer works and I'll move it.",
    },
}


def main() -> None:
    for v in VARIANTS:
        src = HERE / f"tanager_slack_v16{v}.json"
        dst = HERE / f"tanager_slack_v19{v}.json"
        d = json.loads(src.read_text())
        d["note"] = (d.get("note", "") + f" v19: + conditional trigger {TRIGGER['id']} "
                     f"(beat 2; beat 1 is the timed 09:10 ask in scripts/v19_morning.json). "
                     f"World otherwise identical to v16{v}.").strip()
        d["triggers"] = [json.loads(json.dumps(TRIGGER))]
        dst.write_text(json.dumps(d, indent=2, ensure_ascii=False))
        print(f"wrote {dst.name}")


if __name__ == "__main__":
    main()
