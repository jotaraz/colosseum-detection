"""aug2026_v15c: v15b with the sprint channel going online at 09:27.

For the concurrency mechanism's opening: the aug-2026-sprint channel does not exist until
09:27 (world server hides it until then, driven by the new ``channel_online`` fixture
field), and its first message is ops-bot posting the kickoff brief at 09:27:00 — the same
text that is pinned. The runner's ring then sends the members their "ops-bot added you"
notifications staggered from 09:28.

The kickoff message carries a fixture timestamp *after* the 08:55 warm-up start on
purpose: the world server's replay snip turns it into a scheduled delivery, so it lands at
09:27 by the same machinery as every other live message.

Run:  python experiments/agent4/fixtures/build_v15c.py   (requires aug2026_v15b.json)
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "aug2026_v15b.json"
DST = HERE / "aug2026_v15c.json"

ONLINE_AT = "2026-08-10T09:27:00"


def main() -> None:
    data = json.loads(SRC.read_text())
    src_sha = hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:12]

    sprint = next(c for c in data["conversations"] if c["id"] == "C-sprint")
    assert sprint.get("messages") == [] and sprint.get("pinned")
    sprint["messages"] = [{
        "ts": f"{datetime.fromisoformat(ONLINE_AT).timestamp():.6f}",
        "user": "ops-bot",
        "text": sprint["pinned"],
    }]
    data["channel_online"] = {"C-sprint": ONLINE_AT}

    data["version"] = "v15c"
    data["note"] = (
        f"v15b (src sha {src_sha}) + sprint channel online at {ONLINE_AT}: hidden before "
        "then, ops-bot posts the kickoff brief as its first message at creation. Nothing "
        "else changed."
    )
    DST.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {DST}")


if __name__ == "__main__":
    main()
