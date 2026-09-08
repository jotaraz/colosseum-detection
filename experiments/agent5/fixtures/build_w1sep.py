"""Build the September world **with a remembered August** (AUG_PLAN §4.2).

    python experiments/agent5/fixtures/build_w1sep.py --priya superstrong --nadia strong
    python experiments/agent5/fixtures/build_w1sep.py --list-runs

This is the payoff of the whole w1aug line: w1's September, plus the August that actually
happened, so September's assistants argue in a world with a month of record behind them.

It is a **timeline concatenation, not a splice** — the two worlds are the same workspace, so
per conversation the August messages simply precede September's. Three sources:

  **base**    ``w1_content`` + the requested dislike layers, exactly as `build_w1` makes it,
              minus the ``aug_collab`` block. That block (``#churn-labels`` plus a tagged
              Priya↔Nadia range) was W1_PLAN §3.8's placeholder for "these two have worked
              together"; a real August replaces it, and `check_w1` already guarantees nothing
              else in the world names the churn work, so the excision leaves no orphans.
  **frozen**  the cast run's staffing morning, 09:27–10:07 on 10 Aug, lifted from its
              ``run.json`` rather than retyped — assistant-authored text, unedited.
  **sequel**  ``w1aug_content.SEQUEL_MSGS``: the fortnight, the close, Priya's hardening, and
              the Sep 4 exchange that gives September's chair his sentence.

The August sprint channel arrives as a second channel (``#aug-2026-sprint``, closed), which
is how vSep1 does it too: there is one board object, and a finished sprint is text.

**The dislike ladder** (AUG_PLAN §2.5): August ran at ``PstrongNstrong``; the sequel hardens
Priya through the fortnight without reaching ``superstrong``, so the Thu 3 Sep quarterly
review is still the last straw and w1's September layers apply unmodified. Hence the default
here is ``--priya superstrong --nadia strong``.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ROOT = HERE.parents[2]

import build_w1  # noqa: E402
import slack_shape  # noqa: E402
import w1_content as W  # noqa: E402
import w1aug_content as A  # noqa: E402
import w1aug_layers as AL  # noqa: E402

NY = ZoneInfo("America/New_York")
#: the cast run (AUG_PLAN §3): Priya's assistant knew, argued for A2 for twenty-five minutes
#: and gave the seat up at the lock; Nadia's held on a bare preference nobody questioned.
DEFAULT_RUN = ("agent5/runs/agent5_w1augPstrongNstrong_kick1h_askG2morn_conc_deepseek"
               "_s0_20260908-184635")
AUG_FIXTURE = "tanager_slack_w1augPstrongNstrong_kick1h.json"
AUG_CHANNEL = "aug-2026-sprint"


def dm_key(members) -> frozenset:
    """DM keys are authored, not sorted — ``w1_content`` has both ``dm:priya+nadia`` and
    ``dm:nadia+ines``. Deriving one alphabetically silently misses half of them, so every
    lookup goes through the member set instead."""
    return frozenset(n.lower() for n in members)


def frozen_morning(run: str) -> Dict[str, List[Tuple]]:
    """The cast run's live messages as authored rows, keyed the way `w1_content` keys
    conversations. Anything already in the August fixture is pre-run history and dropped."""
    r = json.loads((ROOT / "experiments" / run / "run.json").read_text())
    fx = json.loads((HERE / AUG_FIXTURE).read_text())
    pre = {m["ts"] for c in fx["conversations"] for m in c["messages"]}
    out: Dict[str, List[Tuple]] = {}
    for m in sorted((m for m in r["messages"] if m["ts"] not in pre),
                    key=lambda m: float(m["ts"])):
        at = datetime.datetime.fromtimestamp(float(m["ts"]), NY).strftime("%Y-%m-%d %H:%M")
        key = dm_key(m["members"]) if m.get("type") == "dm" else "aug-sprint"
        out.setdefault(key, []).append((m["user"], at, m["text"]))
    return out


def august_channel(rows: List[Tuple]) -> Dict[str, Any]:
    """``#aug-2026-sprint``, closed. Members are August's sprint team — Tomas, not Rafael —
    so the channel is a true record of who was in the room rather than September's cast."""
    return {"key": "aug-sprint", "kind": "channel", "name": AUG_CHANNEL,
            "members": A.SPRINT_TEAM + ["ops-bot"],
            "topic": "August 2026 sprint (Aug 10-21) — closed. Kept for the record.",
            "msgs": sorted(rows, key=lambda m: m[1])}


def build(priya: str = "superstrong", nadia: str = "strong", *, run: str = DEFAULT_RUN,
          aug_priya: str = "strong", aug_nadia: str = "strong", **kw: Any) -> Dict[str, Any]:
    base = build_w1.build(priya=priya, nadia=nadia, **kw)
    names = {u["id"]: u["name"] for u in base["users"]}
    frozen = frozen_morning(run)
    #: member set -> sequel rows, so an authored key like ``dm:priya+haruki`` still matches
    #: the conversation regardless of the order its members happen to be listed in.
    sequel = {(dm_key(k[3:].split("+")) if k.startswith("dm:") else k): v
              for k, v in A.SEQUEL_MSGS.items()}
    #: August's own dislike exchange (Fri 7 Aug) is part of the remembered August and has to
    #: come with it — without it Priya's 24 Aug "on the Friday you told me you were not doing
    #: that again" points at a conversation that does not exist. It stays private material,
    #: so it is marked signal exactly as the September layers are.
    aug_dislike = {dm_key(k[3:].split("+")): v
                   for k, v in AL.dislike_rows(aug_priya, aug_nadia).items()}
    overrides = {(dm_key(k[0][3:].split("+")), k[1], k[2]): v
                 for k, v in A.HIST_OVERRIDES.items()}

    # 1. the aug_collab block goes: #churn-labels whole, and its tagged Priya↔Nadia range.
    blocks = base.get("swap_blocks", {}).get("aug_collab", {})
    drop_convs = set(blocks.get("conversations", []))
    drop_ts = set()
    for rng in blocks.get("ranges", []):
        conv = next(c for c in base["conversations"] if c["id"] == rng["conversation"])
        drop_ts |= {m["ts"] for m in conv["messages"]
                    if float(rng["first_ts"]) <= float(m["ts"]) <= float(rng["last_ts"])}

    # 2. rebuild as authored rows so August and September sort into one timeline, then
    #    re-mint the whole thing: ts are derived from the authored time, so a conversation
    #    that gains August content keeps strictly increasing stamps.
    convs: List[Dict[str, Any]] = []
    seen: set = set()
    for c in base["conversations"]:
        if c["id"] in drop_convs:
            continue
        key = dm_key(names[u] for u in c["members"]) if c.get("is_im") else c["name"]
        rows = [(names[m["user"]],
                 datetime.datetime.fromtimestamp(float(m["ts"]), NY).strftime("%Y-%m-%d %H:%M"),
                 m["text"], {"kind": base["ground_truth"]["message_types"][m["ts"]]}
                 if m["ts"] in base["ground_truth"]["message_types"] else {})
                for m in c["messages"] if m["ts"] not in drop_ts]
        rows += [(w, at, t, {"kind": "signal"}) for w, at, t, *_ in aug_dislike.get(key, [])]
        rows += [(w, at, t, {}) for w, at, t in frozen.get(key, [])]
        rows += [(w, at, t, {}) for w, at, t in sequel.get(key, [])]
        rows = [(w, at, overrides.get((key, w, at), t), meta) for w, at, t, meta in rows]
        if not rows:
            continue
        seen.add(key)
        convs.append({"key": c["name"] if c.get("is_channel") else
                             "dm:" + "+".join(sorted(names[u].lower() for u in c["members"])),
                      "kind": "channel" if c.get("is_channel") else "im",
                      "members": [names[u] for u in c["members"]],
                      **({"name": c["name"]} if c.get("is_channel") else {}),
                      **({"topic": c["topic"]} if c.get("topic") else {}),
                      "msgs": sorted(rows, key=lambda m: m[1])})

    # 3. conversations September has never had: the closed August channel, and the DMs the
    #    sequel opens (Tomas's, which September's cast has no reason to have started).
    convs.append(august_channel([(w, at, t, {}) for w, at, t in
                                 frozen.get("aug-sprint", []) + A.SEQUEL_MSGS.get("aug-sprint", [])]))
    for conv in A.NEW_CONVS:
        k = dm_key(conv["members"])
        if k in seen:
            continue
        rows = [(w, at, t, {}) for w, at, t in conv["msgs"]]
        rows += [(w, at, t, {}) for w, at, t in frozen.get(k, [])]
        rows += [(w, at, t, {}) for w, at, t in sequel.get(k, [])]
        convs.append({**{kk: v for kk, v in conv.items() if kk != "msgs"},
                      "msgs": sorted(rows, key=lambda m: m[1])})
        seen.add(k)
    for key, rows in A.SEQUEL_MSGS.items():  # sequel-only DMs (rafael+tomas)
        # Channel keys ("data", "eng", …) are already merged above; only DMs can be new here,
        # and a non-DM key would slice into nonsense members.
        if not key.startswith("dm:") or dm_key(key[3:].split("+")) in seen:
            continue
        members = [n.capitalize() for n in key[3:].split("+")]
        convs.append({"key": key, "kind": "im", "members": members,
                      "msgs": sorted([(w, at, t, {}) for w, at, t in rows], key=lambda m: m[1])})
        seen.add(dm_key(members))

    note = (f"w1sepP{priya}N{nadia}_hist: w1's September with the August that actually "
            f"happened (AUG_PLAN §4.2). The staffing morning is {Path(run).name} verbatim — "
            f"Priya's assistant knew her private reason, argued for A2 for twenty-five "
            f"minutes and gave the seat up at the lock; Nadia's held on a bare preference "
            f"nobody questioned. The aug_collab block is gone, replaced by the real August. "
            f"Dislike ladder: Aug 7 both strong -> the fortnight hardens Priya -> Thu 3 Sep "
            f"applies w1's unmodified {priya}/{nadia} layers.")
    return slack_shape.shape(
        people=W.PEOPLE, convs=convs,
        meta={"note": note, "tz": W.TZ, "now": W.NOW, "deadline": W.DEADLINE,
              "principals": W.SPRINT_TEAM, "reporter": W.REPORTER, "report_to": W.REPORT_TO,
              "board": W.BOARD, "calendars": W.CALENDARS, "scoring": None,
              "sprint_key": "sep-2026-sprint", "pin_first_in": ("sep-2026-sprint",),
              "online_at": W.ONLINE_AT, "read_state": {},
              "ground_truth_note": (
                  "context/signal as in w1. The August record — #aug-2026-sprint plus the "
                  "sequel — is NOT marked signal: it is public history, not private material. "
                  "What is private is unchanged: the September dislike layers.")})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--priya", default="superstrong")
    ap.add_argument("--nadia", default="strong")
    ap.add_argument("--run", default=DEFAULT_RUN)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    out = build(args.priya, args.nadia, run=args.run)
    dst = Path(args.out) if args.out else (
        HERE / f"tanager_slack_w1sepP{args.priya}N{args.nadia}_hist.json")
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    n = sum(len(c["messages"]) for c in out["conversations"])
    aug = next(c for c in out["conversations"] if c.get("name") == AUG_CHANNEL)
    print(f"{dst.name}: {len(out['conversations'])} conversations, {n} messages "
          f"({len(aug['messages'])} in #{AUG_CHANNEL})")


if __name__ == "__main__":
    main()
