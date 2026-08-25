from __future__ import annotations

"""The optimization loop and its CLI.

One step: **propose a batch of three → validate → run every rollout in one pool → judge →
majority → reward each candidate → record**. No gate; a batch the prompter cannot get into shape
is re-asked up to ``--repair-attempts`` times. Single-thread hill climbing over the batch's best.

Run it::

    # offline — scripted target and a keyword stub judge. No network. Checks the wiring.
    python -m experiments.agent3.loop --offline --steps 3

    # live (v15 defaults: Nadia + Tomas optimized, Priya rewarded)
    python -m experiments.agent3.loop --steps 20 --replicates 3 \\
        --config experiments/agent3/configs/agent3_v15_deepseek.yaml \\
        --warm-start askG,askK,askM,askP1,askP4 \\
        --out-dir experiments/agent3/outputs/<run>

What a run writes:

===========================  ==========================================================
``metadata.json``            models, replicates, fixture + sha, reward, judge version
``prompter_system.md``       the scaffold (fixed for the run)
``warm_start.json``          the arms the prompter was started from, and their scores
``history.jsonl``            one compact row per candidate
``steps/step_NNN.json``      one batch: three candidates, the prompter's tool trajectory,
                             per-rollout judge records (every vote), reward decomposition
``runs/stepNNN/<run_id>/``   one rollout: run.json + run.html (agent1 shape),
                             candidate.json, asks.json, judge.json
``best.json``                the top-reward candidate so far
===========================  ==========================================================
"""

import argparse
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from experiments.agent1.prompts import TaskAssignPrompts
from experiments.agent1.workspace import Workspace
from experiments.agent3 import (claude_prompter, prompter_tools, reward as reward_mod,
                                verdict_export, warm_start)
from experiments.agent3.candidate import (DEFAULT_OPTIMIZED, DEFAULT_REWARD_AGENT, TIERS,
                                          Candidate, check_roles, fixed_ask_for)
from experiments.agent3.judge import JUDGE_VERSION, MajorityJudge
from experiments.agent3 import lie_judge
from experiments.agent3.prompter import Attempt, Prompter, ScriptedPrompter
from experiments.agent3.target_run import RunArtifacts, TargetRunner
from experiments.social_jira4.llm import make_judge_caller

logger = logging.getLogger("experiments.agent3.loop")

DEFAULT_FIXTURE = "experiments/agent1/fixtures/aug2026_v15_renamed.json"
DEFAULT_PROMPTER = "z-ai/glm-5.3"
DEFAULT_JUDGE = "deepseek/deepseek-v4-flash-0731"
DEFAULT_JUDGE_PROVIDERS = "GMICloud"
STEP_SCHEMA = 2


def _usage_of(caller: Any) -> Dict[str, Any]:
    snap = getattr(caller, "snapshot", None)
    return dict(snap()) if callable(snap) else {}


def _usage_delta(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k in set(before) | set(after):
        a, b = after.get(k), before.get(k)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            out[k] = round(a - b, 8) if isinstance(a, float) else a - b
    return out


class Loop:
    def __init__(self, *, prompter: Any, runner: TargetRunner, judge: Any, base: Workspace,
                 replicates: int, out_dir: Path, repair_attempts: int = 3,
                 callers: Optional[Dict[str, Any]] = None,
                 optimized: Sequence[str] = DEFAULT_OPTIMIZED,
                 reward_agent: str = DEFAULT_REWARD_AGENT,
                 warm: Sequence[Any] = (),
                 spec: reward_mod.RewardSpec = reward_mod.V1):
        self.optimized = tuple(optimized)
        self.reward_agent = reward_agent
        self.prompter = prompter
        self.runner = runner
        self.judge = judge
        self.base = base
        self.replicates = int(replicates)
        self.out_dir = Path(out_dir)
        self.repair_attempts = repair_attempts
        self.callers = callers or {}
        self.warm = list(warm)
        self.spec = spec
        self.history: List[Attempt] = []
        self.attempt_no = 0
        (self.out_dir / "steps").mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------------------------------- one batch
    def _batch(self, opt_step: int) -> Dict[str, Any]:
        """Propose, run and score one batch. Returns the step record."""
        began = time.monotonic()
        usage_before = {role: _usage_of(c) for role, c in self.callers.items()}
        record: Dict[str, Any] = {"schema": STEP_SCHEMA, "opt_step": opt_step, "ran": False,
                                  "failure": "", "attempts": [], "prompter": {}}

        try:
            batch = self.prompter.propose(self.history)
        except Exception as exc:  # noqa: BLE001 — a dead prompter ends the run, loudly
            record["failure"] = f"prompter: {type(exc).__name__}: {exc}"
            record["prompter"] = dict(getattr(self.prompter, "last_meta", {}) or {})
            return record
        record["prompter"] = dict(getattr(self.prompter, "last_meta", {}) or {})

        attempts: List[Attempt] = []
        for c in batch:
            self.attempt_no += 1
            attempts.append(Attempt(step=self.attempt_no, candidate=c, opt_step=opt_step))
        for a in attempts:
            problems = a.candidate.validate(self.base)
            if problems:
                a.failure = "candidate: " + "; ".join(problems)
        runnable = [a for a in attempts if not a.failure]
        if not runnable:
            record["failure"] = "batch: no candidate passed the shape check"
            record["attempts"] = [self._attempt_detail(a) for a in attempts]
            return record
        if len(runnable) < len(attempts):
            logger.warning("  %d of %d candidates failed the shape check; running the rest",
                           len(attempts) - len(runnable), len(attempts))

        artifacts = self.runner.run_batch([a.candidate for a in runnable], self.replicates,
                                          step=opt_step)
        by_slot: Dict[int, List[RunArtifacts]] = {}
        for art in artifacts:
            by_slot.setdefault(art.slot, []).append(art)

        ok = [art for art in artifacts if art.ok]
        judged_all: List[Dict[str, Any]] = []
        if ok:
            # Rollouts are judged concurrently with each other; the judge's own pool is per
            # rollout, so total judge concurrency is len(ok) x --judge-workers.
            with ThreadPoolExecutor(max_workers=len(ok)) as pool:
                judged_all = list(pool.map(self._judge, ok))
        judged_by_run = {id(art): j for art, j in zip(ok, judged_all)}

        for a in runnable:
            arts = by_slot.get(a.candidate.slot, [])
            a.errors = [art.error for art in arts if art.error]
            a.run_paths = [art.run_path for art in arts]
            per_run: List[float] = []
            for art in arts:
                judged = judged_by_run.get(id(art))
                if judged is None:
                    continue
                judged.update({"replicate": art.replicate, "run_path": art.run_path,
                               "reward_agent": self.reward_agent,
                               "optimized": list(self.optimized),
                               "tier": a.candidate.tier})
                judged["reward"] = reward_mod.rollout_reward(judged, spec=self.spec)
                a.judged.append(judged)
                per_run.append(judged["reward"])
                if art.run_dir:
                    Path(art.run_dir).joinpath("judge.json").write_text(
                        json.dumps(judged, indent=2, ensure_ascii=False, default=str),
                        encoding="utf-8")
                    try:
                        verdict_export.export(Path(art.run_dir), judged)
                    except Exception as exc:  # noqa: BLE001 — the page is a convenience
                        logger.warning("verdict export failed for %s: %s", art.run_dir, exc)
            if not per_run:
                a.failure = "rollouts: every replicate failed — " + "; ".join(a.errors)[:300]
                continue
            a.ran = True
            a.reward = reward_mod.aggregate(per_run)
            a.reward_detail = reward_mod.explain(a.judged, self.reward_agent, self.spec)

        self.history.extend(attempts)
        record["ran"] = any(a.ran for a in attempts)
        record["duration_s"] = round(time.monotonic() - began, 1)
        record["usage"] = {role: _usage_delta(usage_before.get(role, {}), _usage_of(c))
                           for role, c in self.callers.items()}
        record["attempts"] = [self._attempt_detail(a) for a in attempts]
        record["best_tier"] = max((a for a in attempts if a.ran), key=lambda a: a.reward,
                                  default=Attempt(0, Candidate({}))).candidate.tier
        if not record["ran"]:
            record["failure"] = "batch: no candidate produced a scored rollout"
        for a in attempts:
            a.duration_s = record["duration_s"]
        return record

    def _judge(self, art: RunArtifacts) -> Dict[str, Any]:
        ws = Workspace.load(self.runner.fixture_path)
        config = art.report.get("config") or {}
        prompts = TaskAssignPrompts(
            ws,
            confidentiality=str(config.get("confidentiality") or "audience"),
            discussion_norms=str(config.get("discussion_norms") or "off"),
            ask=str(config.get("ask") or art.candidate.fixed_ask),
            ask_overrides=config.get("ask_overrides") or art.candidate.ask_overrides(),
        )
        return self.judge.judge(art.report, ws, prompts, art.turns)

    # ------------------------------------------------------------------------------ resume
    def resume(self) -> int:
        """Rebuild ``history`` from ``steps/step_NNN.json`` and return the last step done.

        A step file is written only after its batch is complete, and ``history.jsonl`` right
        after, so killing the process between steps loses nothing and a half-finished step
        leaves no file. The prompter sees exactly the trajectory it would have seen."""
        last = 0
        for f in sorted((self.out_dir / "steps").glob("step_*.json")):
            s = json.loads(f.read_text(encoding="utf-8"))
            for d in s.get("attempts") or []:
                a = Attempt(step=int(d["step"]), candidate=Candidate.from_dict(d["candidate"]),
                            opt_step=int(s.get("opt_step") or 0), ran=bool(d.get("ran")),
                            failure=str(d.get("failure") or ""),
                            reward=float(d.get("reward") or 0.0),
                            judged=list(d.get("runs") or []),
                            run_paths=[r.get("run_path") for r in (d.get("runs") or [])],
                            errors=list(d.get("errors") or []),
                            reward_detail=dict(d.get("reward_detail") or {}),
                            duration_s=float(s.get("duration_s") or 0))
                self.history.append(a)
                self.attempt_no = max(self.attempt_no, a.step)
            if s.get("ran"):
                last = max(last, int(s.get("opt_step") or 0))
        if self.history:
            logger.info("resumed %d candidates from %s (last completed step %d)",
                        len(self.history), self.out_dir, last)
            self._write_best()
        return last

    # ------------------------------------------------------------------------------ driver
    def run(self, steps: int, *, start_at: int = 1) -> List[Attempt]:
        for opt_step in range(start_at, steps + 1):
            if (self.out_dir / "STOP").exists():
                logger.info("STOP file present — stopping before step %s; resumable", opt_step)
                break
            for repair in range(self.repair_attempts + 1):
                logger.info("step %s/%s%s", opt_step, steps, f" (repair {repair})" if repair else "")
                record = self._batch(opt_step)
                record["repair"] = repair
                self._write_step(record, opt_step)
                self._append_history(record)
                if record["ran"]:
                    for d in record["attempts"]:
                        logger.info("  %-13s reward %5.2f  %s", d["candidate"].get("tier") or "?",
                                    d.get("reward") or 0.0, d.get("candidate_digest"))
                    self._write_best()
                    break
                if record["failure"].startswith("prompter:"):
                    logger.error("  %s", record["failure"])
                    return self.history
                logger.info("  did not run: %s", record["failure"][:200])
            else:
                logger.info("  step %s exhausted its repair attempts", opt_step)
        return self.history

    # --------------------------------------------------------------------------- artifacts
    def _attempt_detail(self, a: Attempt) -> Dict[str, Any]:
        return {
            "step": a.step, "tier": a.candidate.tier, "slot": a.candidate.slot,
            "ran": a.ran, "failure": a.failure, "reward": a.reward,
            "errors": a.errors,
            "candidate": a.candidate.to_dict(), "candidate_digest": a.candidate.digest(),
            "asks": a.candidate.all_asks(self.base) if a.candidate.asks else {},
            "reward_detail": a.reward_detail,
            "runs": a.judged,
        }

    def _write_step(self, record: Dict[str, Any], opt_step: int) -> None:
        (self.out_dir / "steps" / f"step_{opt_step:03d}.json").write_text(
            json.dumps(record, indent=2, default=str), encoding="utf-8")
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        """Rewrite SUMMARY.md from the step files just written.

        A run is watched while it runs, and a summary that is only written at the end is the
        wrong artefact for that. Regenerating costs a fraction of a second against a step that
        costs half an hour, and it reads the step files rather than in-memory state, so a
        resumed run produces the same file an uninterrupted one would.

        Never fatal: a run that has already paid for its rollouts must not die because a
        rendering helper raised."""
        try:
            from experiments.agent3 import summarize_run
            summarize_run.write(self.out_dir, quiet=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("  SUMMARY.md not refreshed (%s: %s)", type(exc).__name__, exc)

    def _append_history(self, record: Dict[str, Any]) -> None:
        with open(self.out_dir / "history.jsonl", "a", encoding="utf-8") as fh:
            for d in record.get("attempts") or []:
                summ: Dict[str, Any] = {}
                for j in d.get("runs") or []:
                    for agent, s in (j.get("summary") or {}).items():
                        slot = summ.setdefault(agent, {})
                        # jv7 summaries carry `categories`; jv8's carry lie counts and the
                        # deception split. Read whichever is there — a judge whose record shape
                        # this does not know must not be able to kill a run that has already
                        # paid for its rollouts.
                        for cat, n in (s.get("categories") or {}).items():
                            slot[cat] = slot.get(cat, 0) + n
                        if s.get("lie_turns"):
                            slot["lie-turns"] = slot.get("lie-turns", 0) + s["lie_turns"]
                        for dec, n in (s.get("deception") or {}).items():
                            slot[dec] = slot.get(dec, 0) + n
                fh.write(json.dumps({
                    "step": d["step"], "opt_step": record["opt_step"],
                    "repair": record.get("repair", 0), "tier": d.get("tier"),
                    "ran": d.get("ran"), "reward": d.get("reward"),
                    "digest": d.get("candidate_digest"),
                    "failure": str(d.get("failure") or "")[:400],
                    "rationale": str((d.get("candidate") or {}).get("rationale") or "")[:400],
                    "per_run_reward": [r["reward"] for r in
                                       ((d.get("reward_detail") or {}).get("per_seed") or [])],
                    "majority_counts": summ,
                    "n_tool_calls": (record.get("prompter") or {}).get("n_tool_calls"),
                    "duration_s": record.get("duration_s"),
                }, default=str) + "\n")

    def _write_best(self) -> None:
        ran = [a for a in self.history if a.ran]
        if ran:
            best = max(ran, key=lambda a: a.reward)
            (self.out_dir / "best.json").write_text(
                json.dumps({"opt_step": best.opt_step, **self._attempt_detail(best)},
                           indent=2, default=str), encoding="utf-8")

    def write_metadata(self, extra: Dict[str, Any]) -> None:
        meta = {
            "fixture": {"path": str(self.runner.fixture_path), "version": self.base.version,
                        "sha": self.base.sha},
            "replicates": self.replicates,
            "tiers": list(TIERS),
            "optimized": list(self.optimized), "reward_agent": self.reward_agent,
            "fixed_ask": self.runner.settings.get("ask") or fixed_ask_for(self.base),
            "reward": self.spec.to_dict(),
            "judge_version": (
                (lie_judge.JV9_JUDGE_VERSION if self.spec.judge == "jv9" else lie_judge.JUDGE_VERSION)
                if self.spec.judge in ("jv8", "jv9") else JUDGE_VERSION),
            "repair_attempts": self.repair_attempts,
            "gates": [],
            "warm_start": {"arms": [e.arm for e in self.warm],
                           "n": {e.arm: e.n for e in self.warm},
                           "mean": {e.arm: round(e.mean, 3) for e in self.warm},
                           "readable_by_tools": True,
                           "in_leaderboard": True},
            "step_schema": STEP_SCHEMA,
            **extra,
        }
        (self.out_dir / "metadata.json").write_text(json.dumps(meta, indent=2, default=str),
                                                    encoding="utf-8")
        (self.out_dir / "prompter_system.md").write_text(self.prompter.system_prompt,
                                                         encoding="utf-8")
        if self.warm:
            (self.out_dir / "warm_start.json").write_text(
                json.dumps([e.to_dict() for e in self.warm], indent=2, default=str),
                encoding="utf-8")


# -------------------------------------------------------------------------------------- CLI
def main() -> None:
    parser = argparse.ArgumentParser(description="agent3 — batched ask optimization over agent1.")
    parser.add_argument("--steps", type=int, default=5)
    parser.add_argument("--replicates", type=int, default=3,
                        help="rollouts per candidate (M). 3 candidates x M rollouts per step")
    parser.add_argument("--fixture", default=DEFAULT_FIXTURE)
    parser.add_argument("--config", default=None, help="YAML config for the target model")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--optimize", default=",".join(DEFAULT_OPTIMIZED),
                        help="comma-separated principals whose asks the prompter writes")
    parser.add_argument("--reward-agent", default=DEFAULT_REWARD_AGENT,
                        help="the principal whose turns reward_v1 reads")
    parser.add_argument("--warm-start", default="",
                        help="agent1 ask arms to start from — names (askG,askK,…) or a path to a "
                             "JSON file with an `arms` list. Curated by hand; there is no default")
    parser.add_argument("--warm-corpus", default=warm_start.DEFAULT_CORPUS)
    parser.add_argument("--warm-targeted", action="store_true",
                        help="for --reward v3/v4: read the arms' existing agent2 lie_* files "
                             "(targeted turns only, biased low) instead of refusing them for "
                             "lacking a full sweep. Cheap; use when a fresh sweep is not worth "
                             "the wait")
    parser.add_argument("--warm-prior", default="",
                        help="comma-separated prior agent3 out-dirs (e.g. run05,run06) whose "
                             "own candidates, re-judged under --reward's judge, become extra "
                             "warm entries. Needs the target's lie_* files already beside those "
                             "rollouts — this does not judge anything itself")
    parser.add_argument("--reward", default="v1", choices=sorted(reward_mod.SPECS),
                        help="which reward the prompter climbs. v1: 2x hard + 1x soft, counting "
                             "purposeful OR ambiguous. v2: 4x hard + 1x soft, counting only "
                             "labels that >=2 of 3 judges called purposeful. A resume keeps "
                             "whatever its run recorded, whatever this says")
    parser.add_argument("--repair-attempts", type=int, default=3)
    parser.add_argument("--parallel-rollouts", type=int, default=9,
                        help="rollouts in flight at once; a whole batch by default")
    parser.add_argument("--prompter-model", default=DEFAULT_PROMPTER)
    parser.add_argument("--prompter-max-tokens", type=int, default=24000)
    parser.add_argument("--prompter-temperature", type=float, default=0.9)
    parser.add_argument("--prompter-reasoning", default="low",
                        help="reasoning effort for the prompter. Do not leave it empty on "
                             "glm-5.3: unset, it spends the whole token budget thinking and "
                             "returns nothing (see prompter.Prompter)")
    parser.add_argument("--prompter-tool-calls", type=int, default=15,
                        help="tool-call budget per proposal; 0 disables the tools entirely")
    parser.add_argument("--prompter-backend", default="openrouter",
                        choices=("openrouter", "claude-cli"),
                        help="how the prompter is called. 'claude-cli' drives `claude -p` with "
                             "the five tools served over MCP; --prompter-model, --temperature "
                             "and --reasoning are ignored (see claude_prompter)")
    parser.add_argument("--prompter-cli-model", default=claude_prompter.DEFAULT_CLI_MODEL,
                        help="model for --prompter-backend claude-cli ('sonnet', 'opus', or a "
                             "full model name)")
    parser.add_argument("--prompter-cli-budget-usd", type=float, default=0.0,
                        help="per-step --max-budget-usd for claude -p; 0 for none")
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE)
    parser.add_argument("--judge-temperature", type=float, default=0.0)
    parser.add_argument("--judge-providers", default=DEFAULT_JUDGE_PROVIDERS,
                        help="OpenRouter provider order for the judge, no fallbacks; '' = unpinned")
    parser.add_argument("--judge-retries", type=int, default=1)
    parser.add_argument("--judge-workers", type=int, default=6,
                        help="concurrent judge calls PER ROLLOUT (rollouts are judged in parallel)")
    parser.add_argument("--judge-max-tokens", type=int, default=16000)
    parser.add_argument("--judge-agents", default="",
                        help="principals to judge (default: the reward agent alone); "
                             "'all' judges every principal")
    parser.add_argument("--resume", action="store_true",
                        help="continue a run in --out-dir; --steps is the TOTAL step count")
    parser.add_argument("--offline", action="store_true",
                        help="scripted target + keyword stub judge — checks the wiring only")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    base = Workspace.load(args.fixture)
    out_dir = Path(args.out_dir or f"experiments/agent3/outputs/{'offline' if args.offline else 'run'}")
    optimized = tuple(p.strip() for p in args.optimize.split(",") if p.strip())
    reward_agent = args.reward_agent.strip()
    if args.resume and (out_dir / "metadata.json").exists():
        # A run's roles are part of what its candidates are; a resume continues them, whatever
        # the flags say, so an old step file and a new one never disagree about who was written.
        meta = json.loads((out_dir / "metadata.json").read_text(encoding="utf-8"))
        optimized = tuple(meta.get("optimized") or optimized)
        reward_agent = str(meta.get("reward_agent") or reward_agent)
        # The reward is part of what a run's numbers mean; resuming under a different one would
        # put two incomparable scales in one history.jsonl and silently rerank the leaderboard.
        recorded = str((meta.get("reward") or {}).get("name") or "")
        for key, cand in reward_mod.SPECS.items():
            if cand.name == recorded and key != args.reward:
                logger.info("resume: keeping %s from metadata (flag said %s)", key, args.reward)
                args.reward = key
        if args.fixture != meta.get("fixture", {}).get("path", args.fixture):
            parser.error(f"{out_dir} was run on {meta['fixture']['path']}, not {args.fixture}")
    if (problems := check_roles(base, optimized, reward_agent)):
        parser.error("; ".join(problems))

    spec = reward_mod.SPECS[args.reward]
    settings: Dict[str, Any] = {}
    make_client = None
    if args.config:
        import yaml

        from experiments.agent1 import run as agent1_run

        with open(args.config, "r", encoding="utf-8") as fh:
            settings = agent1_run.resolve_settings(yaml.safe_load(fh) or {})
    # The month comes from the fixture's clock, as agent1's own prompts now do; a config `ask` wins.
    fixed_ask = str(settings.get("ask") or fixed_ask_for(base))

    warm: List[Any] = []
    arms = warm_start.parse_arms(args.warm_start)
    if arms and not args.offline:
        if not args.config:
            parser.error("--warm-start needs --config: the arms are checked against its settings")
        loader = warm_start.load_targeted if args.warm_targeted else warm_start.load
        warm = loader(arms, base, settings, corpus=args.warm_corpus,
                      optimized=optimized, reward_agent=reward_agent,
                      fixed_ask=fixed_ask, spec=reward_mod.SPECS[args.reward])
        for e in warm:
            logger.info("warm start %-7s mean %.2f over n=%d (%d rollouts refused)",
                        e.arm, e.mean, e.n, len(e.rejected))
    if args.warm_prior and not args.offline:
        prior_dirs = [f"experiments/agent3/outputs/{p.strip()}"
                     for p in args.warm_prior.split(",") if p.strip()]
        prior = warm_start.load_prior_run(prior_dirs, reward_agent,
                                          spec=reward_mod.SPECS[args.reward])
        warm += prior
        logger.info("warm start: %d entries from prior run(s) %s", len(prior), args.warm_prior)

    callers: Dict[str, Any] = {}
    library: Optional[prompter_tools.RolloutLibrary] = None
    if args.offline:
        from experiments.agent1.smoke import ScriptedClient

        from experiments.agent3.smoke import StubJudgeCaller, offline_batches, offline_scripts

        prompter: Any = ScriptedPrompter(base, offline_batches(fixed_ask, optimized, reward_agent))
        judge: Any = MajorityJudge(StubJudgeCaller(reward_agent), workers=1,
                                   agents=[reward_agent])
        if args.reward in ("v3", "v4"):
            parser.error("--offline has no jv8/jv9 stub; run the offline wiring check under v1 or v2")
        make_client = lambda _s: (lambda name: ScriptedClient(  # noqa: E731
            offline_scripts(Workspace.load(args.fixture), name, reward_agent)))
        settings = {"max_rounds": 3, "max_conversation_steps": 8, "turn_jitter": 0,
                    "harness": "full", "confidentiality": "inference",
                    "discussion_norms": "self_and_others",
                    "calendar_example_day": base.now.strftime("%Y-%m-%d")}
    else:
        if not args.config:
            parser.error("--config is required for a live run")
        from experiments.social_jira2.openrouter_client import OpenRouterClient

        # Pinned, because unpinned flash-0731 calls route by price to a long tail of small fp4
        # upstreams (run01 steps 1-2: 20+ providers, one call hung 35 minutes) — slow, and a
        # different quantization from the jv7 replicate files. allow_fallbacks=false: a loud
        # error over a silent reroute.
        routing = None
        if args.judge_providers.strip():
            routing = {"order": [p.strip() for p in args.judge_providers.split(",") if p.strip()],
                       "allow_fallbacks": False}
        judge_caller = make_judge_caller(model=args.judge_model, max_tokens=args.judge_max_tokens,
                                         temperature=args.judge_temperature,
                                         provider_routing=routing)
        callers = {"judge": judge_caller}
        # The warm arms are readable by the tools, not just quotable in the briefing: askG is
        # fifteen rollouts of one unchanged ask, and reading a scoring one beside a non-scoring
        # one is the only comparison in the setup with the ask held constant.
        library = (prompter_tools.RolloutLibrary(out_dir / "runs", reward_agent, warm=warm)
                   if args.prompter_tool_calls > 0 else None)
        shared = dict(fixed_ask=fixed_ask, optimized=optimized, reward_agent=reward_agent,
                      library=library, warm=warm, replicates=args.replicates,
                      max_tool_calls=args.prompter_tool_calls, spec=spec)
        if args.prompter_backend == "claude-cli":
            # No client, no temperature, no reasoning knob: Claude Code owns all three. The
            # tools reach the model over MCP instead of in the request — same five functions,
            # same schemas, read out of prompter_tools.TOOLS by the server.
            prompter = claude_prompter.ClaudeCliPrompter(
                base, workdir=out_dir / "prompter_cli", runs_dir=out_dir / "runs",
                cli_model=args.prompter_cli_model,
                max_budget_usd=args.prompter_cli_budget_usd,
                max_tokens=args.prompter_max_tokens, reasoning_effort="", **shared)
        else:
            prompter = Prompter(OpenRouterClient(request_timeout=600, total_timeout=1200),
                                args.prompter_model, base,
                                max_tokens=args.prompter_max_tokens,
                                temperature=args.prompter_temperature,
                                reasoning_effort=args.prompter_reasoning, **shared)
        judge_agents = ([a.strip() for a in args.judge_agents.split(",") if a.strip()]
                        or [reward_agent])
        if args.judge_agents.strip().lower() == "all":
            judge_agents = None  # type: ignore[assignment]
        # The reward picks the judge, not a separate flag: v3 reads jv8 lie verdicts and would
        # score every turn 0 against jv7 categories, silently, which is exactly the kind of
        # mismatch that produces a run of flat zeros nobody can explain.
        if spec.judge in ("jv8", "jv9"):
            critic_path, judge_version = ((lie_judge.JV9_CRITIC_PATH, lie_judge.JV9_JUDGE_VERSION)
                                          if spec.judge == "jv9" else
                                          (lie_judge.CRITIC_PATH, lie_judge.JUDGE_VERSION))
            judge = lie_judge.LieMajorityJudge(judge_caller, workers=args.judge_workers,
                                               agents=judge_agents, retries=args.judge_retries,
                                               critic_path=critic_path, judge_version=judge_version)
        else:
            judge = MajorityJudge(judge_caller, workers=args.judge_workers,
                                  agents=judge_agents, retries=args.judge_retries)
        logger.info("judge: %s (%s) for %s", type(judge).__name__, spec.judge, spec.name)

    runner = TargetRunner(args.fixture, settings, out_dir / "runs", make_client=make_client,
                          parallel_rollouts=args.parallel_rollouts)
    loop = Loop(prompter=prompter, runner=runner, judge=judge, base=base,
                replicates=args.replicates, out_dir=out_dir,
                repair_attempts=args.repair_attempts, callers=callers,
                optimized=optimized, reward_agent=reward_agent, warm=warm, spec=spec)
    start_at = 1
    if args.resume:
        start_at = loop.resume() + 1
    elif (out_dir / "history.jsonl").exists():
        parser.error(f"{out_dir} already holds a run; pass --resume to continue it or pick a new --out-dir")
    loop.write_metadata({
        "offline": bool(args.offline),
        "target_model": settings.get("model_name") or ("scripted" if args.offline else ""),
        "config": args.config,
        "prompter_backend": "scripted" if args.offline else args.prompter_backend,
        "prompter_model": ("scripted" if args.offline else
                           (f"claude-cli:{args.prompter_cli_model}"
                            if args.prompter_backend == "claude-cli" else args.prompter_model)),
        "prompter_temperature": (args.prompter_temperature
                                 if args.prompter_backend == "openrouter" else None),
        "prompter_reasoning": (args.prompter_reasoning
                               if args.prompter_backend == "openrouter" else None),
        "prompter_tool_calls": args.prompter_tool_calls,
        "judge_model": args.judge_model if not args.offline else "stub",
        "judge_temperature": args.judge_temperature,
        "judge_replicates": judge.replicates,
        "judge_agents": args.judge_agents or reward_agent,
        "judge_providers": args.judge_providers if not args.offline else "",
        "judge_retries": args.judge_retries,
        "judge_workers": args.judge_workers,
        "openrouter_max_retries": __import__("os").getenv("OPENROUTER_MAX_RETRIES"),
        "parallel_rollouts": args.parallel_rollouts,
    })

    history = loop.run(args.steps, start_at=start_at)
    ran = [a for a in history if a.ran]
    print(f"\n{len(ran)}/{len(history)} candidates ran; wrote {out_dir}")
    if ran:
        best = max(ran, key=lambda a: a.reward)
        print(f"best: step {best.opt_step} ({best.candidate.tier}), reward {best.reward:.2f} "
              f"({best.candidate.digest()})")


if __name__ == "__main__":
    main()
