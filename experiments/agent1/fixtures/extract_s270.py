from __future__ import annotations

"""Freeze the August run into a committed side-file, for v17 to build on.

v17's world contains the August sprint *as it actually happened* — the argument, the
fabricated dentist, the handover, the report to the manager. That history lives in a run
record under ``outputs/``, which is gitignored, so the v17 builder cannot read it: a fixture
must be reproducible from the repository alone. This script is the one-shot bridge. Run it
once, commit the JSON it writes, and never run it again unless the source run changes.

    PYTHONPATH=. .venv/bin/python experiments/agent1/fixtures/extract_s270.py

Two transformations, both mechanical:

* **Everything at or after the run's first message is taken; everything before is dropped.**
  The earlier messages are already in the v16 fixture, and re-importing them would duplicate
  the world rather than extend it.
* **Names are mapped back to placeholders.** ``build_workspace`` works in Alice/Bob/Carol and
  applies ``NAME_MAP`` as a post-pass, so imported material has to arrive in the same dialect
  or the rename would run twice on it.

Conversation ids follow ``Workspace.open_dm``'s rule — the two names, lowered, in the order
they first spoke — with the fixture's "D-" prefix rather than the runtime's lowercase "d-".
The builder merges these into the v16 world **by member set**, not by id, so a thread that
already exists gains its messages instead of being duplicated under a near-identical name.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from experiments.agent1.fixtures.build_workspace import NAME_MAP  # noqa: E402

SOURCE = "experiments/agent1/outputs/v16/inf_askG_glm53_s270.json"
OUT = Path(__file__).resolve().parent / "aug2026_s270_import.json"

#: The run's clock starts here: Mon 10 Aug 2026, 09:30. Messages before it are v16's.
CUTOFF = 1786347000.0

#: Realistic -> placeholder. The inverse of NAME_MAP, which build_workspace applies forwards.
BACK = {real: placeholder for placeholder, real in NAME_MAP.items()}
_RE = re.compile(r"\b(" + "|".join(BACK) + r")\b")
_RE_LOWER = re.compile(r"\b(" + "|".join(n.lower() for n in BACK) + r")\b")


def unrename(text: str) -> str:
    text = _RE.sub(lambda m: BACK[m.group(1)], text)
    return _RE_LOWER.sub(lambda m: BACK[m.group(1).capitalize()].lower(), text)


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    with open(root / SOURCE, "r", encoding="utf-8") as fh:
        run = json.load(fh)

    threads = {}
    for key, messages in run["transcript"].items():
        fresh = [m for m in messages if float(m["ts"]) >= CUTOFF]
        if not fresh:
            continue
        if key.startswith("#"):
            conv_id, members, kind = "C-sprint", None, "channel"
        else:
            a, b = key[3:].split("+")
            people = [unrename(a), unrename(b)]
            # ``open_dm`` lowercases the whole id ("d-bob-carol"); authored fixture ids
            # keep the "D-" prefix. These are authored history now, so they take the
            # fixture's convention, and the builder merges by member set regardless.
            conv_id = "D-" + f"{people[0]}-{people[1]}".lower()
            members, kind = people, "dm"
        threads[conv_id] = {
            "kind": kind,
            "members": members,
            "messages": [
                {"ts": m["ts"], "user": unrename(m["from"]), "text": unrename(m["text"])}
                for m in fresh
            ],
        }

    payload = {
        "source": SOURCE,
        "source_outcome": run["outcome"],
        "source_config": {k: run["config"].get(k)
                          for k in ("workspace", "confidentiality", "discussion_norms", "seed")},
        "cutoff_ts": f"{CUTOFF:.6f}",
        "note": ("Messages written during inf_askG_glm53_s270, mapped back to placeholder "
                 "names. Frozen because outputs/ is gitignored and a fixture must build from "
                 "the repo alone."),
        "threads": threads,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False)
        fh.write("\n")

    total = sum(len(t["messages"]) for t in threads.values())
    print(f"wrote {OUT.relative_to(root)}: {len(threads)} threads, {total} messages")
    for cid, t in threads.items():
        print(f"  {cid:26} {t['kind']:8} {len(t['messages']):2}")


if __name__ == "__main__":
    main()
