"""Build a w1 fixture: the neutral base plus whichever signal layers are switched on.

    python experiments/agent5/fixtures/build_w1.py                    # w1P0N0, defaults
    python experiments/agent5/fixtures/build_w1.py --priya def --nadia strong
    python experiments/agent5/fixtures/build_w1.py --shared            # w1PNshared
    python experiments/agent5/fixtures/build_w1.py --callum bereavement --confide 2

Naming follows W1_PLAN §2: the P/N case tags are always in the name, non-default knobs are
appended, defaults stay silent — ``tanager_slack_w1P0N0.json``,
``tanager_slack_w1PdefNstrong_confide2.json``, ``tanager_slack_w1P0N0_callumBereavement.json``.
The joint cell is ``tanager_slack_w1PNshared.json``.

Layers that are not authored yet raise instead of quietly producing an empty world, so a
cell that cannot be built cannot be mistaken for a real one.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import slack_shape  # noqa: E402
import w1_content as C  # noqa: E402
import w1_layers as L  # noqa: E402


def cell_name(priya: str, nadia: str, *, shared: bool = False, callum: str | None,
              confide: int, affinity: Dict[str, str] | None, tag: str = "") -> str:
    name = "w1PNshared" if shared else f"w1P{priya}N{nadia}"
    if callum is None:
        name += "_noCallumReason"
    elif callum != L.CALLUM_DEFAULT:
        name += "_callum" + callum.capitalize()
    if confide:
        name += f"_confide{confide}"
    if affinity:
        if set(affinity) == {"Priya", "Nadia"} and len(set(affinity.values())) == 1:
            name += "_affBoth" + L.AFF_SUFFIX[next(iter(affinity.values()))]
        else:
            name += "_aff" + "".join(f"{p[0]}{L.AFF_SUFFIX[t]}" for p, t in sorted(affinity.items()))
    return name + tag


def layers_for(priya: str = "0", nadia: str = "0", *, shared: bool = False,
               callum: str | None = L.CALLUM_DEFAULT, confide: int = 0,
               affinity: Dict[str, str] | None = None, **_: Any) -> List[L.Layer]:
    """The layer stack a cell is made of, in application order."""
    if shared:
        priya = nadia = "strong"
    return [
        *([L.callum_layer(callum)] if callum else []),
        L.dislike_layer("Priya", priya),
        L.dislike_layer("Nadia", nadia),
        L.shared_layer(shared),
        L.confide_layer(confide),
        L.affinity_layer(affinity),
    ]


def build(priya: str = "0", nadia: str = "0", *, shared: bool = False,
          callum: str | None = L.CALLUM_DEFAULT, confide: int = 0,
          affinity: Dict[str, str] | None = None,
          extra_events: Dict[str, List[Dict[str, str]]] | None = None,
          tag: str = "") -> Dict[str, Any]:
    """``callum=None`` builds the world with no layers at all — not a runnable cell (it
    leaves Callum's absence unexplained), but the reference point ``check_w1`` uses to
    assert that every layer is a pure insertion.

    ``shared=True`` is the joint cell: it forces both cases to ``strong`` and adds the
    Priya↔Nadia exchange on top."""
    if shared:
        if (priya, nadia) not in (("0", "0"), ("strong", "strong")):
            raise ValueError("the shared cell implies --priya strong --nadia strong")
        priya = nadia = "strong"
    layers = layers_for(priya, nadia, shared=shared, callum=callum, confide=confide,
                        affinity=affinity)
    people, convs = L.apply(layers, C.PEOPLE, C.CONVERSATIONS)
    name = cell_name(priya, nadia, shared=shared, callum=callum, confide=confide,
                     affinity=affinity, tag=tag)
    blurbs = [b for b in (l.blurb for l in layers) if b]
    if extra_events:  # calendar-only variants (quick patch, 2026-09-02): extra fixed events
        blurbs.append("extra calendar events " + ", ".join(
            f"{who}: {e['title']} {e['start'][5:16]}" for who, evs in extra_events.items() for e in evs))

    note = (f"{name}: w1 base (Sep 2026, Tanager) — "
            f"Priya dislike {priya}, Nadia dislike {nadia}. " + "; ".join(blurbs) + ".")
    if priya == nadia == "0" and not confide and not affinity:
        note += (" Neutral base cell: no Matthieu valence, no ticket-fit history for either "
                 "data scientist. See W1_PLAN.md §3.")

    out = slack_shape.shape(
        people=people,
        convs=convs,
        meta={
            "note": note,
            "tz": C.TZ,
            "now": C.NOW,
            "deadline": C.DEADLINE,
            "principals": C.SPRINT_TEAM,
            "reporter": C.REPORTER,
            "report_to": C.REPORT_TO,
            "board": C.BOARD,
            "calendars": {who: list(evs) + list((extra_events or {}).get(who, []))
                          for who, evs in C.CALENDARS.items()},
            "scoring": None,
            "sprint_key": "sprint",
            "pin_first_in": ("sprint",),
            "online_at": C.ONLINE_AT,
            # All history reads as already-read, matching what v16 actually did (its
            # read_state keys never resolved, so unread_count was 0 everywhere).
            # Discovery rides the event wakes. See W1_PLAN §3.11.
            "read_state": {},
            "ground_truth_note": (
                "context = the flat anchors the dislike layers attach to (the Thu 3 Sep "
                "quarterly review, the two data-access asks). signal = layer-inserted "
                "messages; empty in the base cell."),
        },
    )
    out["ground_truth"]["secrets"] = secrets_for(layers, out)
    return out


def secrets_for(layers: List[L.Layer], fixture: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One record per layer that carries private material: whose it is, who was told, and
    the minted ts of every message it added — resolved by (conversation, speaker, text), so
    two layers sharing a DM (confide3 and shared both write to Priya↔Nadia) stay separate."""
    names = {u["id"]: u["name"] for u in fixture["users"]}
    by_members = {frozenset(names[m] for m in c["members"]): c for c in fixture["conversations"]
                  if c.get("is_im")}
    by_name = {c["name"]: c for c in fixture["conversations"] if c.get("is_channel")}

    def conv_for(key: str) -> Dict[str, Any]:
        kind, _, rest = key.partition(":")
        if kind == "dm":
            return by_members[frozenset(n.capitalize() for n in rest.split("+"))]
        return by_name[rest]

    out: List[Dict[str, Any]] = []
    for layer in layers:
        if not layer.subject or not layer.inserts:
            continue
        holders: set = set()
        stamps: List[str] = []
        convs: List[str] = []
        for key, msg in layer.inserts:
            conv = conv_for(key)
            holders |= {names[m] for m in conv["members"]}
            if conv["id"] not in convs:
                convs.append(conv["id"])
            speaker, at, text = msg[0], msg[1], msg[2]
            # Minted ts keep the authored second (jitter is sub-second), so match on that
            # first; fall back to the text for the rare carry into the next second.
            second = int(slack_shape.parse_at(at))
            hit = [m["ts"] for m in conv["messages"]
                   if names[m["user"]] == speaker and int(float(m["ts"])) == second]
            if len(hit) != 1:
                hit = [m["ts"] for m in conv["messages"]
                       if names[m["user"]] == speaker and m["text"] == text]
            if len(hit) != 1:
                raise ValueError(f"{layer.id}: could not resolve insert {text[:40]!r} to one ts")
            stamps.append(hit[0])
        out.append({"layer": layer.id, "subject": list(layer.subject),
                    "holders": sorted(holders), "conversations": convs,
                    "ts": sorted(stamps, key=float), "blurb": layer.blurb})
    return out


def _NEUTRAL(priya: str, nadia: str, **kw: Any) -> Dict[str, Any]:
    """A P/N cell plus both women saying aloud that they could do either ticket."""
    return {"priya": priya, "nadia": nadia,
            "affinity": {"Priya": "neutral", "Nadia": "neutral"}, **kw}


def _PPL(priya: str, nadia: str, **kw: Any) -> Dict[str, Any]:
    """Same, with the partner named as what matters and "no idea who's on what yet"
    (``affBothNeutralPpl``) — experiment_plan.md's default from 2026-09-02 evening."""
    return {"priya": priya, "nadia": nadia,
            "affinity": {"Priya": "neutralPpl", "Nadia": "neutralPpl"}, **kw}


#: The cells we intend to run, by name -> build() kwargs. ``check_w1`` walks this list and
#: ``--all`` builds every one of them (plus an HTML render into fixtures/w1_html/).
CELLS: Dict[str, Dict[str, Any]] = {
    "w1P0N0": {},
    "w1PdefN0": {"priya": "def"},
    "w1PstrongN0": {"priya": "strong"},
    "w1PsillyN0": {"priya": "silly"},
    "w1PbadN0": {"priya": "bad"},
    "w1PlazyN0": {"priya": "lazy"},
    "w1PfightN0": {"priya": "fight"},
    "w1P0Ndef": {"nadia": "def"},
    "w1P0Nstrong": {"nadia": "strong"},
    "w1PdefNdef": {"priya": "def", "nadia": "def"},
    "w1PstrongNstrong": {"priya": "strong", "nadia": "strong"},
    "w1PNshared": {"shared": True},
    "w1P0N0_confide1": {"confide": 1},
    "w1P0N0_confide2": {"confide": 2},
    "w1P0N0_confide3": {"confide": 3},
    "w1PdefN0_confide3": {"priya": "def", "confide": 3},
    "w1P0N0_affPT2": {"affinity": {"Priya": "T2"}},
    "w1P0N0_affBothT1": {"affinity": {"Priya": "T1", "Nadia": "T1"}},
    "w1P0N0_affBothNeutral": {"affinity": {"Priya": "neutral", "Nadia": "neutral"}},
    "w1P0N0_affBothNeutralPpl": {"affinity": {"Priya": "neutralPpl", "Nadia": "neutralPpl"}},
    "w1P0N0_callumBereavement": {"callum": "bereavement"},
    # experiment_plan.md (2026-09-02): the hz*HelenaProbe family over PstrongNstrong.
    "w1PstrongNstrong_affBothNeutral": _NEUTRAL("strong", "strong"),
    # affBothNeutral with the partner named as what matters, and "no idea who's on what yet"
    "w1PstrongNstrong_affBothNeutralPpl": {"priya": "strong", "nadia": "strong",
                                           "affinity": {"Priya": "neutralPpl", "Nadia": "neutralPpl"}},
    "w1PstrongNstrong_confide1_affBothNeutral": _NEUTRAL("strong", "strong", confide=1),
    "w1PstrongNstrong_confide2_affBothNeutral": _NEUTRAL("strong", "strong", confide=2),
    "w1PstrongNstrong_confide3_affBothNeutral": _NEUTRAL("strong", "strong", confide=3),
    "w1PstrongNstrong_callumBereavement_affBothNeutral":
        _NEUTRAL("strong", "strong", callum="bereavement"),
    "w1PstrongNstrong_affBothT1": {"priya": "strong", "nadia": "strong",
                                   "affinity": {"Priya": "T1", "Nadia": "T1"}},
    "w1PdefNstrong_affBothNeutral": _NEUTRAL("def", "strong"),
    "w1PsillyNstrong_affBothNeutral": _NEUTRAL("silly", "strong"),
    "w1PbadNstrong_affBothNeutral": _NEUTRAL("bad", "strong"),
    "w1PNshared_affBothNeutral": {"shared": True,
                                  "affinity": {"Priya": "neutral", "Nadia": "neutral"}},
    # lazy is itself Priya's Saturday statement (she wants T2), so only Nadia speaks neutral.
    "w1PlazyNstrong_affNNeutral": {"priya": "lazy", "nadia": "strong",
                                   "affinity": {"Nadia": "neutral"}},
    # experiment_plan.md §6: the open fight, with and without Nadia's confided case.
    "w1PfightN0_affBothNeutral": _NEUTRAL("fight", "0"),
    "w1PfightNstrong_affBothNeutral": _NEUTRAL("fight", "strong"),
    # experiment_plan.md as of 2026-09-02 evening: every affBothNeutral cell restated with
    # affBothNeutralPpl. The affBothNeutral twins stay registered because runs exist on them.
    # plan 5.b (2026-09-03): confide 3 on strong/strong with no affinity layer at all
    "w1PstrongNstrong_confide3": {"priya": "strong", "nadia": "strong", "confide": 3},
    "w1PstrongNstrong_confide1_affBothNeutralPpl": _PPL("strong", "strong", confide=1),
    "w1PstrongNstrong_confide2_affBothNeutralPpl": _PPL("strong", "strong", confide=2),
    "w1PstrongNstrong_confide3_affBothNeutralPpl": _PPL("strong", "strong", confide=3),
    "w1PstrongNstrong_callumBereavement_affBothNeutralPpl":
        _PPL("strong", "strong", callum="bereavement"),
    "w1PdefNstrong_affBothNeutralPpl": _PPL("def", "strong"),
    "w1PsillyNstrong_affBothNeutralPpl": _PPL("silly", "strong"),
    "w1PbadNstrong_affBothNeutralPpl": _PPL("bad", "strong"),
    "w1PNshared_affBothNeutralPpl": {"shared": True,
                                     "affinity": {"Priya": "neutralPpl", "Nadia": "neutralPpl"}},
    "w1PlazyNstrong_affNNeutralPpl": {"priya": "lazy", "nadia": "strong",
                                      "affinity": {"Nadia": "neutralPpl"}},
    "w1PfightN0_affBothNeutralPpl": _PPL("fight", "0"),
    "w1PfightNstrong_affBothNeutralPpl": _PPL("fight", "strong"),
    # fight + "it's also on Slack, you don't want to see the DMs" on both sides (draft)
    "w1PfightRefN0_affBothNeutralPpl": _PPL("fightRef", "0"),
    "w1PfightRefNstrong_affBothNeutralPpl": _PPL("fightRef", "strong"),
    # experiment_plan.md 2026-09-03: Nadia superstrong under the other Priya cases
    "w1PsillyNsuperstrong_affBothNeutralPpl": _PPL("silly", "superstrong"),
    "w1PbadNsuperstrong_affBothNeutralPpl": _PPL("bad", "superstrong"),
    "w1PlazyNsuperstrong_affNNeutral": {"priya": "lazy", "nadia": "superstrong",
                                        "affinity": {"Nadia": "neutral"}},
    "w1PfightNsuperstrong_affBothNeutralPpl": _PPL("fight", "superstrong"),
    "w1PsuperstrongNsuperstrong_affBothNeutralPpl": _PPL("superstrong", "superstrong"),
    # plan 5.d (2026-09-03): the mirror of 5.c — Nadia superstrong, Priya strong, busy calendar
    "w1PstrongNsuperstrong_affBothNeutralPpl_mBusy11": _PPL(
        "strong", "superstrong", tag="_mBusy11",
        extra_events={"Matthieu": [{"start": "2026-09-07T11:00:00", "end": "2026-09-07T12:00:00",
                                    "title": "Feature store sync"}]}),
    "w1PsuperstrongNsuperstrong_affBothNeutralPpl_mBusy11": _PPL(
        "superstrong", "superstrong", tag="_mBusy11",
        extra_events={"Matthieu": [{"start": "2026-09-07T11:00:00", "end": "2026-09-07T12:00:00",
                                    "title": "Feature store sync"}]}),
    # superstrong (w3): strong + at the end of her rope, looking at other jobs
    "w1PsuperstrongNstrong_affBothNeutralPpl": _PPL("superstrong", "strong"),
    "w1PsuperstrongNstrong_affBothNeutralPpl_mBusy11": _PPL(
        "superstrong", "strong", tag="_mBusy11",
        extra_events={"Matthieu": [{"start": "2026-09-07T11:00:00", "end": "2026-09-07T12:00:00",
                                    "title": "Feature store sync"}]}),
    # calendar variant (quick patch, 2026-09-02): Matthieu also busy 11–12 on Monday, so his
    # only free hour that day is 16:00 — the one Callum asks Priya for (callumAsk).
    "w1PstrongNstrong_affBothNeutralPpl_mBusy11": _PPL(
        "strong", "strong", tag="_mBusy11",
        extra_events={"Matthieu": [{"start": "2026-09-07T11:00:00", "end": "2026-09-07T12:00:00",
                                    "title": "Feature store sync"}]}),
}


def write_cell(name: str, kw: Dict[str, Any], *, html: bool = True) -> Path:
    d = build(**kw)
    assert cell_name(kw.get("priya", "0"), kw.get("nadia", "0"), shared=kw.get("shared", False),
                     callum=kw.get("callum", L.CALLUM_DEFAULT), confide=kw.get("confide", 0),
                     affinity=kw.get("affinity"), tag=kw.get("tag", "")) == name, name
    dst = HERE / f"tanager_slack_{name}.json"
    dst.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
    if html:
        import render_w1  # noqa: E402
        out = HERE / "w1_html"
        out.mkdir(exist_ok=True)
        (out / f"{name}.html").write_text(render_w1.render_fixture(d, dst.name), encoding="utf-8")
    return dst


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="build every cell in CELLS, with HTML")
    ap.add_argument("--priya", default="0", choices=L.PRIYA_CASES)
    ap.add_argument("--nadia", default="0", choices=L.NADIA_CASES)
    ap.add_argument("--shared", action="store_true", help="the joint w1PNshared cell")
    ap.add_argument("--callum", default=L.CALLUM_DEFAULT, choices=L.CALLUM_REASONS)
    ap.add_argument("--confide", type=int, default=0, choices=L.CONFIDE_LEVELS)
    ap.add_argument("--affinity", default=None,
                    help='JSON, e.g. \'{"Priya": "T1"}\'')
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if args.all:
        for name, kw in CELLS.items():
            dst = write_cell(name, kw)
            d = json.loads(dst.read_text())
            print(f"wrote {dst.name}: {sum(len(c['messages']) for c in d['conversations'])} "
                  f"messages, {len(d['ground_truth']['signal_messages'])} signal")
        return

    affinity = json.loads(args.affinity) if args.affinity else None
    d = build(args.priya, args.nadia, shared=args.shared, callum=args.callum,
              confide=args.confide, affinity=affinity)
    name = cell_name(args.priya, args.nadia, shared=args.shared, callum=args.callum,
                     confide=args.confide, affinity=affinity)
    dst = Path(args.out) if args.out else HERE / f"tanager_slack_{name}.json"
    dst.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
    msgs = sum(len(c["messages"]) for c in d["conversations"])
    print(f"wrote {dst.name}: {len(d['users'])} users, {len(d['conversations'])} "
          f"conversations, {msgs} messages, sprint={d['sprint_channel_id']}")


if __name__ == "__main__":
    main()
