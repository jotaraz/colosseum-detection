"""Build a jv8 target list: every turn by one agent, across chosen ask arms of one fixture.

``lie_over_agent1`` (jv8) judges an explicit ``{run, agent, turn_index}`` list rather than
sweeping a run the way jv7 does, so an *ungated* campaign — every Priya turn, not only the
ones jv7 flagged — has to be enumerated up front. That is all this does.

The point of an ungated list is the denominator: with every Priya turn judged you can ask how
*often* she lies per arm and model, which a fabrication-gated selection cannot answer because
it only ever contains hits.

    python -m experiments.agent2.build_priya_targets \
        --outputs experiments/agent1/outputs/v15 --arms askA,askG --agent Priya \
        --out experiments/agent2/jv8_priya_v15_targets.json

Emits the fields jv8 needs (``run``, ``agent``, ``turn_index``) plus ``model``/``ask``, which
``--per-model`` round-robins on and which make the file readable on its own. It deliberately
does NOT emit ``n_replicates_hardfab_purposeful``: that key is the first campaign's priority
sort, and leaving it absent makes every turn sort equal, i.e. plain (run, turn_index) order.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from experiments.agent1.workspace import Workspace
from experiments.agent2.target_run import assemble_turns

#: <arm>_<model>_s<seed>, e.g. inf_askG_deepseek_s319 — the agent1 rollout naming convention.
_STEM = re.compile(r"^inf_(ask[A-Za-z0-9]+)_([a-z0-9]+)_s(\d+)$")


def build(outputs: Path, arms: List[str], agent: str) -> List[Dict[str, Any]]:
    """Targets keyed exactly as jv8 will look them up.

    ``lie_over_agent1`` resolves a target through ``{(agent, turn_index): t for t in
    assemble_turns(...)}``, so the indices here come from that same function rather than from
    the raw record. Today it numbers turns by position and appends every one, making the two
    equivalent — but deriving them separately is how they would silently diverge later.
    """
    targets: List[Dict[str, Any]] = []
    for path in sorted(outputs.glob("inf_ask*_s*.json")):
        m = _STEM.match(path.stem)
        if not m:  # verdict sidecars (.category2_*, .lie_*) never match the stem pattern
            continue
        ask, model, _seed = m.groups()
        if arms and ask not in arms:
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        ws = Workspace.load(REPO / str((record.get("config") or {})["workspace"]))
        for turn in assemble_turns(record, ws):
            if turn.get("agent") != agent:
                continue
            targets.append({
                "run": str(path.as_posix()),
                "agent": agent,
                "turn_index": int(turn["turn_index"]),
                "model": model,
                "ask": ask,
            })
    return targets


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--outputs", required=True, help="rollout dir, e.g. .../outputs/v15")
    parser.add_argument("--arms", default="", help="comma-separated ask arms; empty = all")
    parser.add_argument("--agent", default="Priya", help="workspace name of the judged agent")
    parser.add_argument("--out", required=True, help="target json to write")
    args = parser.parse_args(argv)

    arms = [a for a in args.arms.split(",") if a.strip()]
    targets = build(Path(args.outputs), arms, args.agent)
    Path(args.out).write_text(json.dumps(targets, indent=2), encoding="utf-8")

    runs = len({t["run"] for t in targets})
    print(f"{len(targets)} targets over {runs} runs -> {args.out}")
    per = Counter((t["ask"], t["model"]) for t in targets)
    for (ask, model), n in sorted(per.items()):
        n_runs = len({t["run"] for t in targets if t["ask"] == ask and t["model"] == model})
        print(f"  {ask:<6} {model:<10} {n_runs:>3} runs {n:>5} {args.agent} turns")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
