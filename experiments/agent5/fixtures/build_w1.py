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
              confide: int, affinity: Dict[str, str] | None) -> str:
    name = "w1PNshared" if shared else f"w1P{priya}N{nadia}"
    if callum is None:
        name += "_noCallumReason"
    elif callum != L.CALLUM_DEFAULT:
        name += "_callum" + callum.capitalize()
    if confide:
        name += f"_confide{confide}"
    if affinity:
        if set(affinity) == {"Priya", "Nadia"} and len(set(affinity.values())) == 1:
            name += "_affBoth" + next(iter(affinity.values())).capitalize()
        else:
            name += "_aff" + "".join(f"{p[0]}{t}" for p, t in sorted(affinity.items()))
    return name


def layers_for(priya: str = "0", nadia: str = "0", *, shared: bool = False,
               callum: str | None = L.CALLUM_DEFAULT, confide: int = 0,
               affinity: Dict[str, str] | None = None) -> List[L.Layer]:
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
          affinity: Dict[str, str] | None = None) -> Dict[str, Any]:
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
                     affinity=affinity)
    blurbs = [b for b in (l.blurb for l in layers) if b]

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
            "calendars": C.CALENDARS,
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
    "w1P0N0_callumBereavement": {"callum": "bereavement"},
}


def write_cell(name: str, kw: Dict[str, Any], *, html: bool = True) -> Path:
    d = build(**kw)
    assert cell_name(kw.get("priya", "0"), kw.get("nadia", "0"), shared=kw.get("shared", False),
                     callum=kw.get("callum", L.CALLUM_DEFAULT), confide=kw.get("confide", 0),
                     affinity=kw.get("affinity")) == name, name
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
