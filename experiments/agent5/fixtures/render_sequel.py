"""Render the September world's August window as a draft, for reading before it is built.

    python experiments/agent5/fixtures/render_sequel.py [--open]

The remembered August has three sources and the only useful review is of all three together,
in one timeline per conversation:

  **base**    w1's own content, which already runs Aug 10 – Sep 7 and is *not* re-authored —
              minus the ``aug_collab`` block (``#churn-labels`` plus the tagged Priya↔Nadia
              range), which the real August replaces (AUG_PLAN §4.2);
  **frozen**  the staffing morning as the cast run actually played it, 09:27–10:07, lifted
              from the run's own messages rather than retyped;
  **sequel**  ``w1aug_content.SEQUEL_MSGS`` — the authored connective tissue from the board
              locking to the September kickoff.

Each is colored, so "did I contradict something the base already says" and "does this
conversation go quiet for three weeks" are both answerable by looking.
"""

from __future__ import annotations

import argparse
import datetime
import html
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ROOT = HERE.parents[2]

import w1_content as W  # noqa: E402
import w1aug_content as A  # noqa: E402

NY = ZoneInfo("America/New_York")
OUT = HERE / "sequel_draft.html"
#: the cast run (AUG_PLAN §3) whose staffing morning this sequel is written against
RUN = ("agent5/runs/agent5_w1augPstrongNstrong_kick1h_askG2morn_conc_deepseek"
       "_s0_20260908-184635")
WINDOW = ("2026-08-10 09:27", "2026-09-07 09:27")
COLOR = {"Priya": "#b3541e", "Matthieu": "#2a6f97", "Nadia": "#6a4c93", "Tomas": "#2d6a4f",
         "Ines": "#8a5a00", "Zofia": "#7a3b6b", "Helena": "#3f5c3a", "ops-bot": "#777"}


def frozen_rows() -> Dict[str, List[Tuple]]:
    """The cast run's live messages, keyed by the conversation key this file uses."""
    run = ROOT / "experiments" / RUN
    r = json.loads((run / "run.json").read_text())
    fx = json.loads((HERE / "tanager_slack_w1augPstrongNstrong_kick1h.json").read_text())
    pre = {m["ts"] for c in fx["conversations"] for m in c["messages"]}
    out: Dict[str, List[Tuple]] = {}
    for m in r["messages"]:
        if m["ts"] in pre:
            continue
        at = datetime.datetime.fromtimestamp(float(m["ts"]), NY).strftime("%Y-%m-%d %H:%M")
        if m.get("type") == "dm":
            key = "dm:" + "+".join(sorted(x.lower() for x in m["members"]))
        else:
            key = (m.get("label") or "").lstrip("#").replace("aug-2026-sprint", "aug-sprint")
        out.setdefault(key, []).append((m["user"], at, m["text"]))
    return out


def collect() -> List[Tuple[str, List[Tuple]]]:
    lo, hi = WINDOW
    # aug_collab is deleted by the arrival of a real August, so it must not appear here.
    block = {(m[0], m[1]) for c in W.CONVERSATIONS if c["key"] == "churn" for m in c["msgs"]}
    tagged = {(m[0], m[1]) for c in W.CONVERSATIONS for m in c["msgs"]
              if len(m) > 3 and isinstance(m[3], dict) and m[3].get("block") == "aug_collab"}
    frozen = frozen_rows()
    keys: List[str] = []
    rows: Dict[str, List[Tuple]] = {}
    seen_keys: set = set()
    for conv in list(W.CONVERSATIONS) + A.NEW_CONVS:
        key = conv["key"]
        # `dm:matthieu+tomas` exists in both the base and w1aug's NEW_CONVS; the base wins.
        if key == "churn" or key in seen_keys:
            continue
        seen_keys.add(key)
        label = conv.get("name") or key
        got = [(w, at, t, "base") for (w, at, t, *_) in
               ((m[0], m[1], m[2]) + tuple(m[3:]) for m in conv["msgs"])
               if lo <= at <= hi and (w, at) not in block and (w, at) not in tagged]
        got += [(w, at, t, "frozen") for w, at, t in frozen.get(key, [])]
        got += [(w, at, t, "sequel") for w, at, t in A.SEQUEL_MSGS.get(key, [])]
        if not got:
            continue
        keys.append(label)
        rows[label] = sorted(got, key=lambda r: r[1])
    # Conversations with no base at all — the closed August sprint channel, and the DMs the
    # sequel invents. `aug-sprint` is where the frozen staffing morning belongs, so its
    # frozen rows are merged here rather than dropped.
    for key in list(A.SEQUEL_MSGS) + [k for k in frozen if k not in A.SEQUEL_MSGS]:
        if key in seen_keys or key in rows:
            continue
        seen_keys.add(key)
        got = [(w, at, t, "frozen") for w, at, t in frozen.get(key, [])]
        got += [(w, at, t, "sequel") for w, at, t in A.SEQUEL_MSGS.get(key, [])]
        keys.append(key)
        rows[key] = sorted(got, key=lambda r: r[1])
    order = ["aug-sprint"]
    keys.sort(key=lambda k: (0 if k in order else 1, k))
    return [(k, rows[k]) for k in keys]


CSS = """
body{margin:0;font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#fbfaf8;color:#1a1a1a}
header{padding:18px 24px;border-bottom:1px solid #e0ddd8;background:#fff;position:sticky;top:0;z-index:3}
h1{margin:0 0 4px;font-size:17px}header p{margin:0;color:#6b6b6b;font-size:13px;max-width:80ch}
main{padding:20px 24px 60px;max-width:1000px}
section{margin:0 0 26px;background:#fff;border:1px solid #e0ddd8;border-radius:8px;padding:14px 16px}
h2{font-size:13px;margin:0 0 10px;text-transform:uppercase;letter-spacing:.06em;color:#6b6b6b}
.m{padding:5px 0 5px 10px;border-left:3px solid transparent;margin-bottom:2px}
.base{border-left-color:#dcd8d1}.frozen{border-left-color:#2a6f97;background:#f4f8fb}
.sequel{border-left-color:#b3541e;background:#fdf6f1}
.meta{font-size:12px;color:#6b6b6b}.who{font-weight:600}.txt{white-space:pre-wrap}
.gap{color:#9b2226;font-size:12px;padding:4px 0 4px 10px}
.key{display:inline-block;padding:1px 7px;border-radius:99px;font-size:11px;margin-right:6px}
.kb{background:#eee;color:#555}.kf{background:#dceaf5;color:#20536f}.ks{background:#f7e3d6;color:#8a3d13}
"""


def render() -> str:
    convs = collect()
    n = {k: sum(1 for r in rows if r[3] == k) for k in ("base", "frozen", "sequel")
         for rows in [sum((r for _, r in convs), [])]}
    parts = [f"<!doctype html><meta charset='utf-8'><title>w1aug sequel — draft</title><style>{CSS}</style>",
             "<header><h1>The remembered August — draft</h1>",
             f"<p>Mon 10 Aug 09:27 → Mon 7 Sep 09:27, per conversation. "
             f"<span class='key kb'>base</span> w1's own content, unchanged, minus the deleted "
             f"aug_collab block · <span class='key kf'>frozen</span> the cast run's staffing "
             f"morning · <span class='key ks'>sequel</span> authored connective tissue "
             f"(<code>w1aug_content.SEQUEL_MSGS</code>). "
             f"{n['base']} base, {n['frozen']} frozen, {n['sequel']} sequel.</p></header><main>"]
    for label, rows in convs:
        parts.append(f"<section><h2>{html.escape(label)} · {len(rows)} messages</h2>")
        prev = None
        for who, at, text, src in rows:
            if prev and (datetime.date.fromisoformat(at[:10])
                         - datetime.date.fromisoformat(prev[:10])).days >= 7:
                parts.append(f"<div class='gap'>— {(datetime.date.fromisoformat(at[:10]) - datetime.date.fromisoformat(prev[:10])).days} days silent —</div>")
            prev = at
            c = COLOR.get(who, "#333")
            parts.append(
                f"<div class='m {src}'><div class='meta'><span class='who' style='color:{c}'>"
                f"{html.escape(who)}</span> · {html.escape(at[5:])}</div>"
                f"<div class='txt'>{html.escape(text)}</div></div>")
        parts.append("</section>")
    return "".join(parts) + "</main>"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()
    OUT.write_text(render())
    convs = collect()
    print(f"wrote {OUT} — {len(convs)} conversations, "
          f"{sum(len(r) for _, r in convs)} messages in the window")
    if args.open:
        subprocess.run(["open", str(OUT)])


if __name__ == "__main__":
    main()
