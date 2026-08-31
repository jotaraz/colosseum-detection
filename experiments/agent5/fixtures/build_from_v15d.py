"""Convert the agent4 v15d fixture into agent5's Slack-native fixture.

Same world — same people, history, signals, board, calendars, 09:27 channel/board
publication — re-expressed in Slack shapes: users get ``U…`` ids, conversations get
``C…``/``D…`` ids with is_channel/is_im flags and created timestamps, messages become raw
Slack message objects, the pinned kickoff becomes a real pin on ops-bot's 09:27 message,
and the pinned-brief *text* of every other channel becomes that channel's topic.

Run:  python experiments/agent5/fixtures/build_from_v15d.py
"""

import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path

# Fixture epochs are minted from naive datetimes: pin the tz so a rebuild anywhere
# produces the same ts (and matches the Berlin tz the served user profiles declare).
os.environ["TZ"] = "Europe/Berlin"
time.tzset()

HERE = Path(__file__).resolve().parent
import argparse
_ap = argparse.ArgumentParser()
_ap.add_argument("--src", default=str(HERE.parents[1] / "agent4" / "fixtures" / "aug2026_v15d.json"))
_ap.add_argument("--out", default=str(HERE / "tanager_slack_v1.json"))
_args = _ap.parse_args()
SRC = Path(_args.src)
DST = Path(_args.out)

# Slack ids are opaque and random — an id that encodes the workspace name (U100TANAG)
# is a tell. Mint hash-derived ids instead: random-looking, but deterministic per
# (prefix, key) so rebuilds are reproducible and diffs stay small.
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def jitter_ts(conv_key: str, ts_f: float, prev: float) -> float:
    """Real Slack ts carry server-assigned microsecond suffixes; the source fixture's
    whole-second stamps were caught as a mock signature. Deterministic per message,
    strictly increasing per conversation."""
    h = int.from_bytes(hashlib.sha256(f"ts:{conv_key}:{ts_f}".encode()).digest()[:4], "big")
    t = int(ts_f) + (h % 999_000 + 500) / 1e6
    if prev is not None and t <= prev:
        t = prev + (h % 900 + 100) / 1e6
    return t


def mint_id(prefix: str, key: str, taken: set) -> str:
    for n in range(1000):
        h = hashlib.sha256(f"slack-id:{prefix}:{key}:{n}".encode()).digest()
        sid = prefix + "".join(_ALPHABET[b % 36] for b in h[:10])
        if sid not in taken:
            taken.add(sid)
            return sid
    raise RuntimeError(f"id space exhausted for {prefix}:{key}")


def main() -> None:
    d = json.loads(SRC.read_text())
    src_sha = hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]

    taken: set = set()
    uid = {}
    users = []
    for i, u in enumerate(d["users"]):
        uid[u["name"]] = mint_id("U", u["name"], taken)
        users.append({
            "id": uid[u["name"]], "name": u["name"], "real_name": u.get("full_name") or u["name"],
            "title": u.get("title") or "", "is_bot": bool(u.get("is_bot")),
            **({"status": u["status"]} if u.get("status") else {}),
        })

    convs = []
    cid_map = {}
    sprint_id = None
    for i, c in enumerate(d["conversations"]):
        is_channel = c.get("type", "channel") == "channel"
        new_id = mint_id("C" if is_channel else "D", c["id"], taken)
        cid_map[c["id"]] = new_id
        messages = []
        prev = None
        for m in c.get("messages", []):
            prev = jitter_ts(c["id"], float(m["ts"]), prev)
            messages.append({"type": "message", "ts": f"{prev:.6f}",
                             "user": uid[m["user"]], "text": m["text"]})
        conv = {
            "id": new_id,
            "members": [uid[m] for m in c["members"]],
            "created": min([float(m["ts"]) for m in c.get("messages", [])]
                           or [datetime(2026, 7, 1).timestamp()]) - 86400,
            "messages": messages, "pins": [],
        }
        if is_channel:
            conv["is_channel"] = True
            conv["is_private"] = True
            conv["name"] = c["name"]
            if c.get("pinned"):
                if c["id"] == "C-sprint":
                    # the kickoff brief IS ops-bot's 09:27 message; pin it
                    assert messages and "SPRINT KICKOFF" in messages[0]["text"]
                    conv["pins"] = [messages[0]["ts"]]
                else:
                    conv["topic"] = c["pinned"][:250]
        else:
            conv["is_im"] = True
        if c["id"] == "C-sprint":
            sprint_id = new_id
        convs.append(conv)

    out = {
        "version": "slack_v1",
        "note": f"agent4 {SRC.stem} (src sha {src_sha}) in Slack shapes; world content identical.",
        "now": d["now"], "deadline": d.get("deadline"),
        "principals": d["principals"], "reporter": d.get("reporter"),
        "report_to": d.get("report_to"),
        "sprint_channel_id": sprint_id,
        "users": users, "conversations": convs,
        "board": d.get("board"), "calendars": d.get("calendars"),
        "scoring": d.get("scoring"), "read_state": d.get("read_state"),
        "ground_truth": d.get("ground_truth"),
        "channel_online": {cid_map[k]: v for k, v in (d.get("channel_online") or {}).items()},
        "board_online": d.get("board_online"),
    }
    assert sprint_id
    DST.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {DST}: {len(users)} users, {len(convs)} conversations, sprint={sprint_id}")


if __name__ == "__main__":
    main()
