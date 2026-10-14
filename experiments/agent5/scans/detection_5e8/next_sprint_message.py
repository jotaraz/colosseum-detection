"""Annotate detection_5e8_v2/reasoning/summary.md with what the seat said next in public.

The judge's **Response** block quotes only the moves it chose to cite, which are often private
(notes and pushes to the seat's own employee) and are sometimes *earlier* than the step the same
row flags as the first sign of doubt. This script adds two things to each detection section and
nothing else:

* an ordering flag, when a quoted response move predates the flagged step;
* the next message the seat actually posted, after both the flagged step and the last quoted
  response move -- added whenever the section has no quoted *post* at or after the flagged step;
* the first DM it sent after that same point, which is where a seat takes something to one person
  rather than to the room -- added wherever one exists and is not already printed in the section.

It is idempotent: it strips the blocks it added before recomputing them, so it can be re-run over
a regenerated summary (``detection_judge5.summarize()`` does not preserve them).
"""
import json, re, sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE.parents[1]))
from experiments.agent5.detection_judge5 import _fixture_for, seat_turns  # noqa: E402
from experiments.agent5 import turn_context5 as tc  # noqa: E402

SUMMARY = HERE / "outputs/detection_5e8_v2/reasoning/summary.md"
SPRINT = "#sep-2026-sprint"
# every line this script writes starts with one of these, so it can remove its own work
FLAG = "*Ordering — "
BLOCK = re.compile(r"^(?:\*\*No response quoted\*\*|\*next (?:sprint-channel message|message "
                   r"posted)|\*Nothing posted after this|\*first DM after)")


class Written:
    """The seat's outgoing record: notes/pushes to its employee and posts, in order.

    Mirrors ``detection_judge5.Evidence._written`` so the ``where`` strings match the ones the
    summary prints, and adds a sort key (turn, step, note-before-tool-call).
    """

    def __init__(self, run_dir: str):
        self.run = json.loads((HERE / "runs" / run_dir / "run.json").read_text())
        _, fixture = _fixture_for(self.run)
        self.tz = tc.Names(self.run, fixture).tz
        self.messages = sorted(self.run.get("messages") or [], key=lambda m: float(m["ts"]))
        self._by_seat = {}

    def _clock(self, ts):
        return datetime.fromtimestamp(float(ts), self.tz).strftime("%a %H:%M")

    def of(self, seat):
        if seat in self._by_seat:
            return self._by_seat[seat]
        out = []
        for i in seat_turns(self.run, seat):
            rec = self.run["turns"][i]
            start = str(rec.get("clock") or "")[11:16]
            for d in rec.get("steps_detail") or []:
                if (t := str(d.get("text") or "").strip()):
                    out.append({"key": (i, d.get("step") or 0, 0), "turn": i, "kind": "note",
                                "where": f"note to {seat}, T{i} step {d.get('step')}, "
                                         f"turn start {start}", "text": t})
            for c in rec.get("tool_calls") or []:
                args = c.get("args") or {}
                key = (i, c.get("step") or 0, 1)
                if c.get("tool") in ("chat_postMessage", "chat_update") and tc._ok(c):
                    res = c.get("result") or {}
                    ts = str(res.get("ts") or "")
                    conv = next((m.get("label") for m in self.messages
                                 if m.get("conv_id") == (args.get("channel") or res.get("channel"))),
                                args.get("channel"))
                    out.append({"key": key, "turn": i, "kind": "post", "conv": conv,
                                "where": f"post in {conv}, T{i}"
                                         + (f", {self._clock(ts)}" if ts else ""),
                                "text": str(args.get("text") or "")})
                elif c.get("tool") == "notify_user" and tc._ok(c):
                    out.append({"key": key, "turn": i, "kind": "push",
                                "where": f"push to {seat}, T{i}, turn start {start}",
                                "text": str(args.get("text") or "")})
        out.sort(key=lambda w: w["key"])
        self._by_seat[seat] = out
        return out


def strip_added(part: str) -> str:
    """Remove the flag line and the appended block from a section, leaving the generator's text."""
    lines, kept, i = part.rstrip("\n").splitlines(), [], 0
    rule = next(n for n, l in enumerate(lines) if l.strip() == "---")
    while i < len(lines):
        l = lines[i]
        if i < rule and l.startswith(FLAG):
            i += 2 if i + 1 < len(lines) and not lines[i + 1].strip() else 1
            continue
        if i < rule and BLOCK.match(l):
            i = rule                          # everything from here to the rule is ours
            continue
        kept.append(l)
        i += 1
    return "\n".join(kept).rstrip("\n") + "\n"   # one \n; re.split ate the blank line


def append_block(part: str, block: str) -> str:
    """Put ``block`` at the end of a detection section, before its closing rule.

    The rule is the section's *first* bare ``---`` line: excerpts are blockquoted, so nothing
    earlier can look like one, and the last section of a run carries the next run's heading (and,
    at the end of the file, the hand-written audits) after that rule.
    """
    lines = part.rstrip("\n").splitlines()
    i = next(n for n, l in enumerate(lines) if l.strip() == "---")
    head = "\n".join(lines[:i]).rstrip("\n")
    return head + "\n\n" + block + "\n" + "\n".join(lines[i:]) + "\n"


def quote(text: str) -> str:
    return "\n".join("> " + l if l.strip() else ">" for l in text.strip().splitlines())


def main(write=False):
    text = SUMMARY.read_text()
    section_run, run_now = {}, None
    for line in text.splitlines():
        if (m := re.match(r'^`(agent5_\S+)`$', line)):
            run_now = m.group(1)
        elif (m := re.match(r'### D(\d+) ', line)):
            section_run[m.group(1)] = run_now
    runs, out = {}, []
    stats = {"flagged": 0, "added_sprint": 0, "added_elsewhere": 0, "nothing_after": 0,
             "has_public_response": 0, "no_response": 0, "added_dm": 0,
             "dm_is_next_message": 0}
    for part in re.split(r'\n(?=### D\d+ )', text):
        if not part.startswith("### D"):
            out.append(part)
            continue
        part = strip_added(part)
        d_no = re.match(r'### D(\d+) ', part).group(1)
        seat = re.match(r'### D\d+ · \S+ s\d+ · (\w+) doubts', part).group(1)
        ft, fs = re.search(r'· first T(\d+) step (\d+) in ', part).groups()
        flagged = (int(ft), int(fs), 0)
        entries = Written(section_run[d_no]) if section_run[d_no] not in runs else runs[section_run[d_no]]
        runs[section_run[d_no]] = entries
        entries = entries.of(seat)
        by_where = {e["where"]: e for e in entries}

        cited = []
        if "**Response**" in part:
            for h in re.findall(r'^\*(.+?):\*$', part.split("**Response**", 1)[1], re.M):
                if h in by_where:
                    cited.append(by_where[h])
                elif (m := re.search(r"reasoning, T(\d+) step (\d+)", h)):
                    cited.append({"key": (int(m.group(1)), int(m.group(2)), 0), "kind": "reasoning"})
        else:
            stats["no_response"] += 1

        early = [c for c in cited if c["key"] < flagged]
        if early:
            stats["flagged"] += 1
            where = ", ".join(f"T{c['key'][0]}" for c in early)
            one = len(early) == 1
            part = part.replace("**Response**\n", "**Response**\n\n" + FLAG
                                + f"{len(early)} of the {len(cited)} move"
                                  f"{'' if len(cited) == 1 else 's'} the judge cites as the "
                                  f"response ({where}) predate{'s' if one else ''} the flagged "
                                  f"step (T{ft} step {fs}). The judge reached back to an earlier "
                                  f"message; the turn label marks where it found a quotable "
                                  f"sentence, not when the doubt formed.*\n", 1)

        anchor = max([c["key"] for c in cited] + [flagged])
        later = [e for e in entries if e["key"] > anchor and e["kind"] == "post"]
        shown = {c.get("where") for c in cited}
        blocks, nxt = [], None
        if any(c["kind"] == "post" and c["key"] >= flagged for c in cited):
            stats["has_public_response"] += 1      # the judge already quotes a public move
        else:
            nxt = next((e for e in later if e.get("conv") == SPRINT), None)
            tag = "next sprint-channel message"
            if nxt is None:
                nxt = next(iter(later), None)
                tag = "next message posted (no later post in the sprint channel)"
            if nxt is None:
                stats["nothing_after"] += 1
                blocks.append(f"*Nothing posted after this — {seat}'s assistant sent no further "
                              f"message to anyone; the moves above stayed between it and "
                              f"{seat}.*\n")
            else:
                stats["added_sprint" if tag.startswith("next sprint") else "added_elsewhere"] += 1
                shown.add(nxt["where"])
                blocks.append(f"*{tag} — {nxt['where']}:*\n\n{quote(nxt['text'])}\n")
        dm = next((e for e in later if str(e.get("conv") or "").startswith("dm:")
                   and e["where"] not in shown), None)
        if dm is not None:
            stats["added_dm"] += 1
            blocks.append(f"*first DM after the flagged step — {dm['where']}:*\n\n"
                          f"{quote(dm['text'])}\n")
        elif nxt is not None and str(nxt.get("conv") or "").startswith("dm:"):
            stats["dm_is_next_message"] += 1       # already printed just above
        if not blocks:
            out.append(part)
            continue
        if "**Response**" not in part:
            blocks[0] = ("**No response quoted** — the judge scored this one `none_visible`.\n\n"
                         + blocks[0])
        out.append(append_block(part, "\n".join(blocks)))
    if write:
        SUMMARY.write_text("\n".join(out))   # re.split consumed one \n per section boundary
    print(stats)


if __name__ == "__main__":
    main(write="--write" in sys.argv)
