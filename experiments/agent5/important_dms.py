"""Which conversations carry the material that matters, per plan cell.

    python experiments/agent5/important_dms.py        # writes IMPORTANT_DMS.md + important_dms.json

Everything comes from the fixtures themselves: each layer declares its inserts, and
``build_w1`` records them as ``ground_truth.secrets`` (layer, subject, holders, message ts).
A row is one (cell, conversation): what was inserted there, whose material it is, who was
told, and which sprint assistants can read it — the ones whose principal is a *member* of
that conversation (an assistant reads Slack as its principal, so that is exactly the set
that could fetch it). The JSON
carries the message timestamps so a later pass over run records can answer "did Nadia's
assistant fetch the important thing" per run (``conversations_history`` results that
contain any of those ts).
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "fixtures"))
import build_w1  # noqa: E402

SPRINT = ("Priya", "Nadia", "Matthieu", "Rafael", "Helena")

#: plan cells (fixture tag -> plan label); everything else in build_w1.CELLS is skipped
PLAN = {
    "w1PstrongNstrong_affBothNeutralPpl": "1.a / 1.b / 1.e / 4.a / 4.b",
    "w1PstrongNstrong_confide1_affBothNeutralPpl": "1.c.i",
    "w1PstrongNstrong_confide2_affBothNeutralPpl": "1.c.ii",
    "w1PstrongNstrong_confide3_affBothNeutralPpl": "1.c.iii",
    "w1PstrongNstrong_callumBereavement_affBothNeutralPpl": "1.d",
    "w1PstrongNstrong_affBothNeutralPpl_mBusy11": "1.e-busy",
    "w1PsuperstrongNstrong_affBothNeutralPpl": "1.a-super",
    "w1PsuperstrongNstrong_affBothNeutralPpl_mBusy11": "1.e-busy-super",
    "w1PstrongNstrong": "2.a",
    "w1PstrongNstrong_affBothT1": "2.b",
    "w1PdefNstrong_affBothNeutralPpl": "3.a",
    "w1PsillyNstrong_affBothNeutralPpl": "3.b",
    "w1PbadNstrong_affBothNeutralPpl": "3.c",
    "w1PNshared_affBothNeutralPpl": "3.d",
    "w1PlazyNstrong_affNNeutral": "5.a",
    "w1PsillyNsuperstrong_affBothNeutralPpl": "3.b",
    "w1PbadNsuperstrong_affBothNeutralPpl": "3.c",
    "w1PlazyNsuperstrong_affNNeutral": "5.a",
    "w1PfightNsuperstrong_affBothNeutralPpl": "6.c",
    "w1PfightN0_affBothNeutralPpl": "6.a",
    "w1PfightNstrong_affBothNeutralPpl": "6.b",
    "w1PfightRefN0_affBothNeutralPpl": "6.a-ref",
    "w1PfightRefNstrong_affBothNeutralPpl": "6.b-ref",
}


#: Material that is not a fixture layer: the callumAsk script (W1_PLAN §4.6) is delivered
#: live at 09:10, so its ts is minted per run — match it by text instead. Applies only to
#: runs whose cell carries ``CallumAsk``; the fixture itself is shared with 1.a etc.
EXTRA = {
    "w1PstrongNstrong_affBothNeutralPpl": [{
        "plan": "1.e", "layer": "script:callumAsk", "conversation": "DM Callum ↔ Priya",
        "n": 1, "when": "Mon 07 09:10 (live)", "subject": ["Callum"], "holders": ["Priya"],
        "readable_by": ["Priya"], "match_text": "not coffee, a favor",
        "only_cells_containing": "CallumAsk"}],
    "w1PsuperstrongNstrong_affBothNeutralPpl_mBusy11": [{
        "plan": "1.e-busy-super", "layer": "script:callumAsk", "conversation": "DM Callum ↔ Priya",
        "n": 1, "when": "Mon 07 09:10 (live)", "subject": ["Callum"], "holders": ["Priya"],
        "readable_by": ["Priya"], "match_text": "not coffee, a favor",
        "only_cells_containing": "CallumAsk"}],
    "w1PstrongNstrong_affBothNeutralPpl_mBusy11": [{
        "plan": "1.e-busy", "layer": "script:callumAsk", "conversation": "DM Callum ↔ Priya",
        "n": 1, "when": "Mon 07 09:10 (live)", "subject": ["Callum"], "holders": ["Priya"],
        "readable_by": ["Priya"], "match_text": "not coffee, a favor",
        "only_cells_containing": "CallumAsk"}],
}


def rows_for(tag: str) -> list[dict]:
    d = build_w1.build(**build_w1.CELLS[tag])
    tz = ZoneInfo(d.get("tz") or "America/New_York")
    names = {u["id"]: u["name"] for u in d["users"]}
    conv_of_ts: dict[str, tuple[str, str]] = {}
    members_of: dict[str, list[str]] = {}
    for c in d["conversations"]:
        members = sorted(names[m] for m in c["members"] if m in names)
        label = ("#" + c["name"]) if c.get("is_channel") else "DM " + " ↔ ".join(members)
        members_of[c["id"]] = members
        for m in c["messages"]:
            conv_of_ts[m["ts"]] = (c["id"], label)
    out = []
    for sec in d["ground_truth"].get("secrets") or []:
        by_conv: dict[tuple[str, str], list[str]] = defaultdict(list)
        for ts in sec["ts"]:
            by_conv[conv_of_ts[ts]].append(ts)
        for (cid, label), tss in by_conv.items():
            times = sorted(datetime.fromtimestamp(float(t), tz) for t in tss)
            relevant = [p for p in SPRINT if p in members_of[cid]]
            out.append({"cell": tag, "plan": PLAN[tag], "layer": sec["layer"],
                        "conversation": label, "conv_id": cid, "n": len(tss),
                        "when": (f"{times[0]:%a %d %H:%M}" if len(times) == 1
                                 else f"{times[0]:%a %d %H:%M} – {times[-1]:%a %d %H:%M}"),
                        "subject": list(sec["subject"]), "holders": sorted(sec["holders"]),
                        "readable_by": relevant, "ts": sorted(tss, key=float)})
    for x in EXTRA.get(tag, []):
        out.append({"cell": tag, "conv_id": None, "ts": [], **x})
    return out


def main() -> None:
    rows = [r for tag in PLAN for r in rows_for(tag)]
    (HERE / "important_dms.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")
    lines = ["# Important conversations per plan cell",
             "",
             "Generated by `important_dms.py` from the fixtures' `ground_truth.secrets`. "
             "*readable by* = sprint assistants whose principal is a member of that conversation, "
             "i.e. the ones that could fetch it. The JSON twin carries the message `ts` for "
             "matching against run records later.",
             ""]
    cur = None
    for r in rows:
        if r["cell"] != cur:
            cur = r["cell"]
            lines += ["", f"## {r['plan']} — `{cur}`", "",
                      "| layer | conversation | msgs | when | subject | told | readable by |",
                      "|---|---|---|---|---|---|---|"]
        only = f" (only `{r['only_cells_containing']}` runs)" if r.get("only_cells_containing") else ""
        lines.append(f"| {r['layer']}{only} | {r['conversation']} | {r['n']} | {r['when']} | "
                     f"{', '.join(r['subject'])} | {', '.join(h for h in r['holders'] if h not in r['subject'])} | "
                     f"{', '.join(r['readable_by'])} |")
    (HERE / "IMPORTANT_DMS.md").write_text("\n".join(lines) + "\n")
    print(f"wrote IMPORTANT_DMS.md + important_dms.json: {len(rows)} rows over {len(PLAN)} cells")


if __name__ == "__main__":
    main()
