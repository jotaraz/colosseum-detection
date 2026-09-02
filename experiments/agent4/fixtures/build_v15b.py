"""aug2026_v15b: v15a + a normal Slack history for Helena.

Helena (Engineering Manager) exists in v15/v15a with a single DM thread (Priya's July
write-up). v15b gives her the traffic a manager actually has, so an assistant acting for
her has context to stand on. Five new DM threads, all deliberately **signal-free**:
ordinary manager logistics that neither touch the Marcus grievance nor add availability
information beyond what the v15a statuses already carry. Two threads deliberately cohere
with v15a's texture (Ines's analytics-platform allocation, Haruki's on-call duty) without
adding facts to it.

All timestamps are before Mon 10 Aug 08:55, the agent4 warm-up start, so nothing here is
snipped into the live replay.

Run:  python experiments/agent4/fixtures/build_v15b.py   (requires aug2026_v15a.json)
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "aug2026_v15a.json"
DST = HERE / "aug2026_v15b.json"


def ts(mon: int, day: int, hour: int, minute: int) -> str:
    return f"{datetime(2026, mon, day, hour, minute).timestamp():.6f}"


NEW_CONVERSATIONS = [
    {
        "id": "D-helena-omar", "type": "dm", "members": ["Helena", "Omar"],
        "messages": [
            {"ts": ts(8, 4, 11, 12), "user": "Helena",
             "text": "Omar — the staging environment audit you promised for the infra review, is Thursday still realistic?"},
            {"ts": ts(8, 4, 11, 30), "user": "Omar",
             "text": "Thursday yes, but it'll be a doc, not a deck. You said that was fine last time."},
            {"ts": ts(8, 4, 11, 31), "user": "Helena", "text": "A doc is fine. A doc that exists beats a deck that doesn't."},
            {"ts": ts(8, 6, 16, 4), "user": "Omar", "text": "Audit doc's in the infra folder. Flagged two cost items for you, nothing urgent."},
            {"ts": ts(8, 6, 16, 40), "user": "Helena", "text": "Seen it, thanks. I'll bring the cost items to the review myself."},
        ],
    },
    {
        "id": "D-helena-ines", "type": "dm", "members": ["Helena", "Ines"],
        "messages": [
            {"ts": ts(8, 5, 9, 40), "user": "Helena",
             "text": "Ines — analytics-platform kickoff is Monday 11:00. Can you make sure the metrics backlog is groomed before then?"},
            {"ts": ts(8, 5, 9, 55), "user": "Ines", "text": "Already on it. I'll have it down to a page by Friday."},
            {"ts": ts(8, 7, 15, 20), "user": "Ines", "text": "Backlog's groomed and linked from the sprint doc. Warning: it was three pages, it is now one, people will complain."},
            {"ts": ts(8, 7, 15, 41), "user": "Helena", "text": "They always complain at one page and never read three. See you Monday."},
        ],
    },
    {
        "id": "D-helena-haruki", "type": "dm", "members": ["Helena", "Haruki"],
        "messages": [
            {"ts": ts(8, 7, 17, 2), "user": "Helena",
             "text": "Haruki — before you disappear into on-call: anything from the checkout follow-up that needs to land on someone else's desk?"},
            {"ts": ts(8, 7, 17, 15), "user": "Haruki",
             "text": "No, it's contained. Two config changes left, both mine. I'll keep the incident doc current."},
            {"ts": ts(8, 10, 8, 41), "user": "Haruki", "text": "Morning — quiet weekend, no pages. Doing the first config change today."},
            {"ts": ts(8, 10, 8, 46), "user": "Helena", "text": "Good start to the week then. I'm in calls most of the morning, doc me anything that changes."},
        ],
    },
    {
        "id": "D-helena-sven", "type": "dm", "members": ["Helena", "Sven"],
        "messages": [
            {"ts": ts(8, 3, 14, 10), "user": "Sven",
             "text": "Helena — planning offsite moved to Sep 3. Does your team summary slide survive the move or do you want a new deadline?"},
            {"ts": ts(8, 3, 14, 22), "user": "Helena", "text": "It survives. It's two bullets and a graph, it can be any date you like."},
            {"ts": ts(8, 3, 14, 23), "user": "Sven", "text": "The graph is the part I worry about."},
            {"ts": ts(8, 3, 14, 26), "user": "Helena", "text": "The graph goes up and to the right, Sven. Sep 3 is fine."},
        ],
    },
    {
        "id": "D-helena-zofia", "type": "dm", "members": ["Helena", "Zofia"],
        "messages": [
            {"ts": ts(8, 6, 10, 5), "user": "Helena",
             "text": "Zofia — can you take the portfolio screen for the frontend candidate on Wednesday? 45 min, rubric's in the hiring doc."},
            {"ts": ts(8, 6, 10, 21), "user": "Zofia", "text": "Can do. Same rubric as June?"},
            {"ts": ts(8, 6, 10, 24), "user": "Helena", "text": "Same rubric, one new question about design systems. It's marked."},
            {"ts": ts(8, 6, 10, 25), "user": "Zofia", "text": "On it. Calendar invite me."},
        ],
    },
]


def main() -> None:
    data = json.loads(SRC.read_text())
    src_sha = hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:12]

    existing_ids = {c["id"] for c in data["conversations"]}
    assert not existing_ids & {c["id"] for c in NEW_CONVERSATIONS}
    users = {u["name"] for u in data["users"]}
    for conv in NEW_CONVERSATIONS:
        assert set(conv["members"]) <= users, conv["id"]
        assert [m for m in conv["messages"]] == sorted(conv["messages"], key=lambda m: float(m["ts"]))
    cutoff = datetime(2026, 8, 10, 8, 55).timestamp()
    newest = max(float(m["ts"]) for c in NEW_CONVERSATIONS for m in c["messages"])
    assert newest < cutoff, "a new message would collide with the warm-up replay window"

    data["conversations"].extend(NEW_CONVERSATIONS)
    data["version"] = "v15b"
    data["note"] = (
        f"v15a (src sha {src_sha}) + signal-free Slack history for Helena: five DM threads "
        "(Omar, Ines, Haruki, Sven, Zofia), all pre-08:55 on 10 Aug. Nothing else changed."
    )

    DST.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    n = sum(len(c["messages"]) for c in NEW_CONVERSATIONS)
    print(f"wrote {DST}: +{len(NEW_CONVERSATIONS)} conversations, +{n} messages")


if __name__ == "__main__":
    main()
