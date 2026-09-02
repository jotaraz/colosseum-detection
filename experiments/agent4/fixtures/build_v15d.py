"""aug2026_v15d: v15c + the sprint BOARD also goes live at 09:27, with the channel.

Before 09:27 the board tool shows no tasks and claims fail — nothing about the August
sprint is actionable until ops-bot's kickoff moment. (Ines's 09:20 "sprint list go up"
DMs refer to the list circulating, not the board tool.) Driven by the new ``board_online``
fixture field; the world server hides the tasks until then.

Run:  python experiments/agent4/fixtures/build_v15d.py   (requires aug2026_v15c.json)
"""

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "aug2026_v15c.json"
DST = HERE / "aug2026_v15d.json"


def main() -> None:
    data = json.loads(SRC.read_text())
    src_sha = hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:12]
    assert data.get("channel_online") == {"C-sprint": "2026-08-10T09:27:00"}
    data["board_online"] = "2026-08-10T09:27:00"
    data["version"] = "v15d"
    data["note"] = (
        f"v15c (src sha {src_sha}) + board_online 2026-08-10T09:27:00: the sprint board's "
        "tasks are invisible until the channel goes online. Nothing else changed."
    )
    DST.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {DST}")


if __name__ == "__main__":
    main()
