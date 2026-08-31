"""Convert agent1's September sequel into agent5's Slack-native vSep1 fixture.

Source: ``experiments/agent1/fixtures/sep2026_v17_renamed.json`` (the renamed cast is the
one agent5's world descends from). vSep1 is a NEW world for agent5 — it does not continue
agent5's own v16/v17 lineage; its remembered August is agent1's frozen s270 run, not the
tanager a/b/c/z variants.

Differences from build_from_v15d.py, all forced by the source shape:
- agent1 fixtures keep the kickoff brief only in a channel's ``pinned`` attribute. Both
  sprint channels get it re-expressed the agent5 way: an ops-bot message at 09:27 of
  their sprint's first morning, pinned. Filler channels' pinned text becomes the topic.
- ``read_state`` and ``ground_truth`` are remapped into the converted coordinates (minted
  conversation ids, jittered ts). The earlier converter copied them verbatim, which left
  them dead; here the unread badge is the discovery mechanism, so it must survive.
- ``calendar-bot`` is added as a bot user: the source has calendar-bot DMs whose author
  has no account, and slack_world.calendar_notify needs the account to deliver invites.
- ``channel_online``/``board_online`` are synthesized (2026-09-07T09:27:00): the source
  has no publication mechanic; agent5's runner delivers the 09:27 member_joined wake.

Run:  python experiments/agent5/fixtures/build_vSep1.py
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
SRC = HERE.parents[1] / "agent1" / "fixtures" / "sep2026_v17_renamed.json"
DST = HERE / "tanager_slack_vSep1.json"

ONLINE_AT = "2026-09-07T09:27:00"
#: Sprint channels whose pinned brief becomes a pinned ops-bot message at 09:27 of the
#: sprint's first morning. Everything else with a pinned brief gets it as the topic.
KICKOFF_AT = {"C-sprint": datetime(2026, 8, 10, 9, 27),
              "C-sep-sprint": datetime(2026, 9, 7, 9, 27)}

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

    src_users = list(d["users"])
    assert not any(u["name"] == "calendar-bot" for u in src_users)
    src_users.append({"name": "calendar-bot", "title": "Calendar automation", "is_bot": True})

    taken: set = set()
    uid = {}
    users = []
    for u in src_users:
        uid[u["name"]] = mint_id("U", u["name"], taken)
        users.append({
            "id": uid[u["name"]], "name": u["name"], "real_name": u.get("full_name") or u["name"],
            "title": u.get("title") or "", "is_bot": bool(u.get("is_bot")),
            **({"status": u["status"]} if u.get("status") else {}),
        })

    convs = []
    cid_map = {}
    #: old ts string -> new ts string, per conversation (for read_state) and globally
    #: (for ground_truth; collisions across conversations are recorded and refused).
    ts_map_by_conv: dict = {}
    ts_map_global: dict = {}
    sprint_id = None
    for c in d["conversations"]:
        is_channel = c.get("type", "channel") == "channel"
        new_id = mint_id("C" if is_channel else "D", c["id"], taken)
        cid_map[c["id"]] = new_id
        src_msgs = list(c.get("messages", []))
        if c["id"] in KICKOFF_AT:
            assert c.get("pinned") and "SPRINT KICKOFF" in c["pinned"], c["id"]
            at = KICKOFF_AT[c["id"]].timestamp()
            assert not src_msgs or at < float(src_msgs[0]["ts"]), \
                f"{c['id']}: kickoff would not be the first message"
            src_msgs.insert(0, {"ts": f"{at:.6f}", "user": "ops-bot", "text": c["pinned"],
                                "_kickoff": True})
        messages = []
        conv_ts_map = {}
        prev = None
        pin_ts = None
        for m in src_msgs:
            prev = jitter_ts(c["id"], float(m["ts"]), prev)
            new_ts = f"{prev:.6f}"
            messages.append({"type": "message", "ts": new_ts,
                             "user": uid[m["user"]], "text": m["text"]})
            if m.get("_kickoff"):
                pin_ts = new_ts
            else:
                conv_ts_map[m["ts"]] = new_ts
                ts_map_global.setdefault(m["ts"], set()).add(new_ts)
        ts_map_by_conv[c["id"]] = conv_ts_map
        conv = {
            "id": new_id,
            "members": [uid[m] for m in c["members"]],
            "created": min([float(m["ts"]) for m in src_msgs]
                           or [datetime(2026, 7, 1).timestamp()]) - 86400,
            "messages": messages, "pins": [pin_ts] if pin_ts else [],
        }
        if is_channel:
            conv["is_channel"] = True
            conv["is_private"] = True
            conv["name"] = c["name"]
            if c.get("pinned") and c["id"] not in KICKOFF_AT:
                conv["topic"] = c["pinned"][:250]
        else:
            conv["is_im"] = True
        if c["id"] == "C-sep-sprint":
            sprint_id = new_id
        convs.append(conv)

    # read_state: viewer -> {cid: last-read ts}. Remap the cid, and move the marker to
    # the jittered ts of the last message at or before it, so the > comparison keeps
    # exactly the same messages unread as in the source.
    read_state = {}
    for viewer, marks in (d.get("read_state") or {}).items():
        read_state[viewer] = {}
        for old_cid, marker in marks.items():
            src_conv = next(c for c in d["conversations"] if c["id"] == old_cid)
            read_upto = [m["ts"] for m in src_conv["messages"]
                         if float(m["ts"]) <= float(marker) + 1e-6]
            assert read_upto, f"read_state marker before every message in {old_cid}"
            read_state[viewer][cid_map[old_cid]] = ts_map_by_conv[old_cid][read_upto[-1]]

    # A source ts can map to several new ts: the mirrored Priya/Nadia signals are sent at
    # the same instant in both confidant DMs (the symmetry design), and agent1's per-ts
    # labels apply to every message carrying that stamp. Expand rather than pick.
    def remap_ts(old_ts: str) -> list:
        news = ts_map_global.get(old_ts) or set()
        assert news, f"ground_truth ts {old_ts} matches no message"
        return sorted(news)

    gt = d.get("ground_truth") or {}
    ground_truth = {
        "signal_messages": [n for t in gt.get("signal_messages", []) for n in remap_ts(t)],
        "message_types": {n: v for t, v in gt.get("message_types", {}).items()
                          for n in remap_ts(t)},
    }

    out = {
        "version": "vSep1",
        "note": f"agent1 {SRC.stem} (src sha {src_sha}) in Slack shapes: the September "
                "sequel — August's sprint frozen as reference, the action on 7 Sep. A new "
                "world for agent5, not a continuation of the tanager v16-v20 lineage.",
        "now": d["now"], "deadline": d.get("deadline"),
        "principals": d["principals"], "reporter": d.get("reporter"),
        "report_to": d.get("report_to"),
        "sprint_channel_id": sprint_id,
        "users": users, "conversations": convs,
        "board": d.get("board"), "calendars": d.get("calendars"),
        "read_state": read_state,
        "ground_truth": ground_truth,
        "channel_online": {sprint_id: ONLINE_AT},
        "board_online": ONLINE_AT,
    }
    assert sprint_id
    DST.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {DST.name}: {len(users)} users, {len(convs)} conversations, "
          f"sprint={sprint_id}, {sum(len(c['messages']) for c in convs)} messages")


if __name__ == "__main__":
    main()
