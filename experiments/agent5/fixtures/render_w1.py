"""Render a w1 (or any agent5 Slack) fixture as a readable, searchable HTML page.

agent1's ``workspace_viewer`` already answers the question this needs answered — "what was
already there", with search across every message, the ground-truth tag shown where it sits,
and the board and calendars on their own pages. It reads agent1's shape, so this de-Slacks
the fixture first: user ids back to names, ``is_channel`` back to ``type``, the pinned
message back to a ``pinned`` attribute.

The swap block and the layer inserts are surfaced as message tags, because when reading a
w1 cell the two questions are always "is this neutral" and "what did the layer add".

    python experiments/agent5/fixtures/render_w1.py                       # w1P0N0
    python experiments/agent5/fixtures/render_w1.py path/to/fixture.json --open
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))

from experiments.agent1.workspace_viewer import render  # noqa: E402


def deslack(d: Dict[str, Any]) -> Dict[str, Any]:
    names = {u["id"]: u["name"] for u in d["users"]}
    blocks = d.get("swap_blocks") or {}
    block_convs = {cid: tag for tag, b in blocks.items() for cid in b["conversations"]}
    block_ranges = [(r["conversation"], float(r["first_ts"]), float(r["last_ts"]), tag)
                    for tag, b in blocks.items() for r in b["ranges"]]

    tags = dict((d.get("ground_truth") or {}).get("message_types") or {})
    convs = []
    for c in d["conversations"]:
        pinned = None
        msgs = []
        for m in c["messages"]:
            if m["ts"] in (c.get("pins") or []):
                pinned = m["text"]
                continue
            msgs.append({"ts": m["ts"], "user": names[m["user"]], "text": m["text"]})
            in_block = block_convs.get(c["id"]) or next(
                (tag for cid, lo, hi, tag in block_ranges
                 if cid == c["id"] and lo <= float(m["ts"]) <= hi), None)
            if in_block:
                tags[m["ts"]] = in_block
        conv = {
            "id": c["id"],
            "type": "channel" if c.get("is_channel") else "im",
            "name": c.get("name"),
            "members": [names[u] for u in c["members"]],
            "messages": msgs,
        }
        if pinned:
            conv["pinned"] = pinned
        if c.get("topic"):
            conv["pinned"] = conv.get("pinned") or c["topic"]
        convs.append(conv)

    users = [{"name": u["name"], "title": u.get("title", ""),
              "department": u.get("status", ""), "is_bot": u.get("is_bot")}
             for u in d["users"]]
    read_state = {who: {cid: ts for cid, ts in marks.items()}
                  for who, marks in (d.get("read_state") or {}).items()}
    return {**{k: v for k, v in d.items()
               if k in ("version", "note", "now", "deadline", "principals", "reporter",
                        "report_to", "board", "calendars")},
            "users": users, "conversations": convs, "read_state": read_state,
            "ground_truth": {**(d.get("ground_truth") or {}), "message_types": tags}}


def render_fixture(d: Dict[str, Any], name: str) -> str:
    """Render a Slack-shaped fixture, with message times shown in the fixture's own zone.

    agent1's viewer formats timestamps in the *browser's* zone (``toLocaleString`` with no
    ``timeZone``), which put a 4:20 PM New York message at 10:20 PM on a Berlin laptop.
    """
    html = render(deslack(d), name)
    tz = d.get("tz") or "Europe/Berlin"
    patched = html.replace('toLocaleString(undefined, {weekday: "short",',
                           f'toLocaleString(undefined, {{timeZone: "{tz}", weekday: "short",')
    if patched == html:
        raise RuntimeError("render_w1: the viewer's time formatter changed; tz patch did not apply")
    return patched


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("fixture", nargs="?", default=str(HERE / "tanager_slack_w1P0N0.json"))
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--open", dest="open_it", action="store_true")
    args = ap.parse_args()

    path = Path(args.fixture)
    d = json.loads(path.read_text())
    out = Path(args.out) if args.out else path.with_suffix(".html")
    out.write_text(render_fixture(d, path.name), encoding="utf-8")
    print(f"wrote {out}")
    if args.open_it:
        subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", str(out)],
                       check=False)


if __name__ == "__main__":
    main()
