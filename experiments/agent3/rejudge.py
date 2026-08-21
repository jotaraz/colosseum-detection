from __future__ import annotations

"""Run the majority judge over finished agent1 run records.

The same ``MajorityJudge`` the loop uses, applied offline — for vote-agreement checks, for
re-judging an optimization run's rollouts under a later judge, or for scoring agent1 arm runs on
the agent3 reward so they are comparable with the loop's steps. Writes
``<run_stem>.agent3_<JUDGE_VERSION>_<model>.json`` beside each record.

    python -m experiments.agent3.rejudge experiments/agent1/outputs/v17/inf_askG_glm_s305.json
"""

import argparse
import json
import logging
from pathlib import Path

from experiments.agent1.prompts import TaskAssignPrompts
from experiments.agent1.workspace import Workspace
from experiments.agent2.target_run import assemble_turns
from experiments.agent3 import reward as reward_mod
from experiments.agent3.judge import JUDGE_VERSION, MajorityJudge
from experiments.social_jira4.llm import make_judge_caller

logger = logging.getLogger("experiments.agent3.rejudge")
REPO = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description="agent3 majority judge over agent1 run records")
    parser.add_argument("runs", nargs="+")
    parser.add_argument("--judge-model", default="deepseek/deepseek-v4-flash-0731")
    parser.add_argument("--judge-temperature", type=float, default=0.0)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--max-tokens", type=int, default=16000)
    parser.add_argument("--roles", default="", help="comma-separated titles (default: all principals)")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    caller = make_judge_caller(model=args.judge_model, max_tokens=args.max_tokens,
                               temperature=args.judge_temperature)
    roles = [r for r in args.roles.split(",") if r.strip()] or None
    judge = MajorityJudge(caller, workers=args.workers, roles=roles)
    slug = args.judge_model.replace("/", "-").replace(".", "").replace("-", "")
    for run in args.runs:
        run_path = Path(run)
        out_path = run_path.with_name(f"{run_path.stem}.agent3_{JUDGE_VERSION}_{slug}.json")
        if out_path.exists() and not args.force:
            logger.info("skip (exists): %s", out_path)
            continue
        report = json.loads(run_path.read_text(encoding="utf-8"))
        config = report.get("config") or {}
        ws = Workspace.load(REPO / str(config["workspace"]))
        prompts = TaskAssignPrompts(
            ws, confidentiality=str(config.get("confidentiality") or "audience"),
            discussion_norms=str(config.get("discussion_norms") or "off"),
            ask=config.get("ask"), ask_overrides=config.get("ask_overrides") or {})
        before = caller.snapshot()
        judged = judge.judge(report, ws, prompts, assemble_turns(report, ws))
        after = caller.snapshot()
        judged["run"] = str(run_path)
        judged["reward_v1"] = reward_mod.rollout_reward(judged)
        judged["reward_detail"] = reward_mod.explain([judged])
        judged["usage"] = {k: (after.get(k, 0) - before.get(k, 0)) for k in after
                           if isinstance(after.get(k), (int, float))}
        out_path.write_text(json.dumps(judged, indent=2, ensure_ascii=False, default=str),
                            encoding="utf-8")
        logger.info("%s: reward_v1=%.1f  %s  $%.4f -> %s", run_path.name, judged["reward_v1"],
                    {a: s["categories"] for a, s in judged["summary"].items()},
                    judged["usage"].get("cost_usd", 0.0), out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
