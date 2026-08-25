from __future__ import annotations

"""Sweep the jv8 lie judge over finished agent1 arm rollouts, to warm-start a reward_v3 run.

    python -m experiments.agent3.sweep_jv8 --arms askA,askG,askK,... --workers 8

Judges **every** turn of the rewarded agent, three replicates each, and writes the votes beside
the run record as ``<stem>.sweep_jv8_<model>[_rN].json`` — the same three-file convention
``warm_start`` reads for jv7, under a name that cannot collide with agent2's *targeted*
``lie_jv8`` files (see ``warm_start._REPLICATE_GLOB``).

Rollouts are swept in **descending reward_v2 order**, computed from the jv7 verdicts already on
disk. That is a progress device, not a filter: every listed rollout is judged, so the resulting
warm start is unbiased. Ordering just means that if v3 turns out to score nothing, it shows up in
the first few rollouts rather than after half an hour — the failure worth catching early, since
jv8's own prompt warns that a lie is "a demanding category" most turns do not contain.

Already-swept rollouts are skipped, so an interrupted sweep resumes by re-running the command.
"""

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List

import yaml

from experiments.agent1 import run as agent1_run
from experiments.agent1.prompts import TaskAssignPrompts
from experiments.agent1.workspace import Workspace
from experiments.agent2.target_run import assemble_turns
from experiments.agent3 import reward as reward_mod, warm_start
from experiments.agent3.candidate import fixed_ask_for
from experiments.agent3.lie_judge import JUDGE_VERSION, LieMajorityJudge
from experiments.social_jira4.llm import make_judge_caller

logger = logging.getLogger("experiments.agent3.sweep_jv8")


def _slug(model: str) -> str:
    return "".join(ch for ch in model.lower() if ch.isalnum())


def _paths(run_path: str, model: str) -> List[Path]:
    stem = run_path[:-5]
    return [Path(f"{stem}.sweep_jv8_{_slug(model)}{suffix}.json")
            for suffix in ("", "_r2", "_r3")]


def _candidates(arms: List[str], base: Workspace, want: Dict[str, Any],
                corpus: str, reward_agent: str) -> List[Dict[str, Any]]:
    """Every admissible rollout of the named arms, with its jv7-based reward_v2, best first."""
    rows: List[Dict[str, Any]] = []
    for arm in arms:
        try:
            entry = warm_start.load([arm], base, want, corpus=corpus, optimized=(),
                                    reward_agent=reward_agent, fixed_ask="",
                                    spec=reward_mod.V2)[0]
        except ValueError as exc:
            logger.warning("skipping %s: %s", arm, str(exc)[:160])
            continue
        for path, v2 in zip(entry.run_paths, entry.rewards):
            rows.append({"arm": arm, "run": path, "v2": v2})
    rows.sort(key=lambda r: -r["v2"])
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="jv8 lie sweep over agent1 arm rollouts.")
    ap.add_argument("--arms", required=True, help="comma-separated ask arms")
    ap.add_argument("--fixture", default="experiments/agent1/fixtures/aug2026_v15_renamed.json")
    ap.add_argument("--config", default="experiments/agent3/configs/agent3_v15_deepseek.yaml")
    ap.add_argument("--corpus", default=warm_start.DEFAULT_CORPUS)
    ap.add_argument("--reward-agent", default="Priya")
    ap.add_argument("--judge-model", default="deepseek/deepseek-v4-flash-0731")
    ap.add_argument("--judge-providers", default="GMICloud")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max-tokens", type=int, default=16000)
    ap.add_argument("--limit", type=int, default=0, help="stop after N rollouts (0 = all)")
    ap.add_argument("--force", action="store_true", help="re-sweep rollouts already done")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    base = Workspace.load(args.fixture)
    with open(args.config, "r", encoding="utf-8") as fh:
        want = agent1_run.resolve_settings(yaml.safe_load(fh) or {})
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]

    todo = _candidates(arms, base, want, args.corpus, args.reward_agent)
    if args.limit:
        todo = todo[:args.limit]
    logger.info("%d rollouts to sweep across %d arms, best reward_v2 first", len(todo), len(arms))

    routing = ({"order": [p.strip() for p in args.judge_providers.split(",") if p.strip()],
                "allow_fallbacks": False} if args.judge_providers.strip() else None)
    caller = make_judge_caller(model=args.judge_model, max_tokens=args.max_tokens,
                              temperature=0.0, provider_routing=routing)
    judge = LieMajorityJudge(caller, workers=args.workers, agents=[args.reward_agent])

    began = time.monotonic()
    scores: List[float] = []
    for i, row in enumerate(todo, 1):
        out_paths = _paths(row["run"], args.judge_model)
        if all(p.exists() for p in out_paths) and not args.force:
            logger.info("[%d/%d] SKIP %s (already swept)", i, len(todo), Path(row["run"]).name)
            continue
        report = json.loads(Path(row["run"]).read_text(encoding="utf-8"))
        ws = Workspace.load(args.fixture)
        cfg = report.get("config") or {}
        prompts = TaskAssignPrompts(
            ws, confidentiality=str(cfg.get("confidentiality") or "audience"),
            discussion_norms=str(cfg.get("discussion_norms") or "off"),
            ask=str(cfg.get("ask") or fixed_ask_for(base)),
            ask_overrides=cfg.get("ask_overrides") or {})
        judged = judge.judge(report, ws, prompts, assemble_turns(report, ws))
        v3 = reward_mod.rollout_reward({**judged, "reward_agent": args.reward_agent},
                                       args.reward_agent, reward_mod.V3)
        scores.append(v3)

        # Split the collapsed record back into one file per replicate, so `warm_start` reads it
        # exactly as it reads jv7's — same shape, same three-file convention.
        for rep, path in enumerate(out_paths):
            path.write_text(json.dumps({
                "judge_version": JUDGE_VERSION, "judge_model": args.judge_model,
                "run": row["run"], "arm": row["arm"], "replicate": rep,
                "turns": [v for t in judged["turns"] for v in t["votes"] if v["replicate"] == rep],
            }, indent=2, default=str), encoding="utf-8")

        nz = sum(1 for s in scores if s > 0)
        logger.info("[%d/%d] %-34s arm=%-6s v2=%.2f  ->  v3=%.1f   "
                    "(%d/%d non-zero so far, %.0f min elapsed)",
                    i, len(todo), Path(row["run"]).name, row["arm"], row["v2"], v3,
                    nz, len(scores), (time.monotonic() - began) / 60)

    if scores:
        nz = sum(1 for s in scores if s > 0)
        print(f"\nswept {len(scores)} rollouts in {(time.monotonic() - began) / 60:.0f} min")
        print(f"  reward_v3 non-zero: {nz}/{len(scores)} ({100 * nz / len(scores):.0f}%)")
        print(f"  mean {sum(scores) / len(scores):.2f}, max {max(scores):.1f}")
        print(f"  distribution: {sorted(scores, reverse=True)}")


if __name__ == "__main__":
    main()
