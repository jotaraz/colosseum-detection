from __future__ import annotations

"""The optimization loop and its CLI.

One step: **propose → validate → roll out (seeds in parallel) → judge ×3 → majority → reward →
record**. No gate; a candidate that fails the shape check is re-proposed in place up to
``--repair-attempts`` times. Single-thread hill climbing.

Run it::

    # offline — scripted target and a keyword stub judge. No network. Checks the wiring.
    python -m experiments.agent3.loop --offline --steps 3

    # live
    python -m experiments.agent3.loop --steps 20 --seeds 1,2,3 \\
        --config experiments/agent3/configs/agent3_v17_glm52.yaml \\
        --prompter-model deepseek/deepseek-v4-pro-0813 \\
        --judge-model deepseek/deepseek-v4-flash-0731

What a run writes:

===========================  ==========================================================
``metadata.json``            models, seeds, fixture + sha, reward, judge version
``prompter_system.md``       the scaffold (fixed for the run)
``history.jsonl``            one compact row per attempt
``steps/step_NNN.json``      the full record of one attempt: candidate, prompter meta,
                             per-seed judge records (every vote), reward decomposition
``runs/stepNNN/<run_id>/``   one rollout per seed: run.json + run.html (agent1 shape),
                             candidate.json, asks.json, judge.json
``best.json``                the top-reward attempt so far
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
from experiments.agent3 import reward as reward_mod
from experiments.agent3 import verdict_export
from experiments.agent3.candidate import (DEFAULT_OPTIMIZED, DEFAULT_REWARD_AGENT, Candidate,
                                          check_roles, fixed_ask_for)
from experiments.agent3.judge import JUDGE_VERSION, MajorityJudge
from experiments.agent3.prompter import Attempt, Prompter, ScriptedPrompter
from experiments.agent3.target_run import RunArtifacts, TargetRunner
from experiments.social_jira4.llm import make_judge_caller, make_prompter_caller

logger = logging.getLogger("experiments.agent3.loop")

DEFAULT_FIXTURE = "experiments/agent1/fixtures/sep2026_v17_renamed.json"
DEFAULT_PROMPTER = "deepseek/deepseek-v4-pro-0813"
DEFAULT_JUDGE = "deepseek/deepseek-v4-flash-0731"
STEP_SCHEMA = 1


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
                 seeds: Sequence[int], out_dir: Path, repair_attempts: int = 3,
                 callers: Optional[Dict[str, Any]] = None,
                 optimized: Sequence[str] = DEFAULT_OPTIMIZED,
                 reward_agent: str = DEFAULT_REWARD_AGENT):
        self.optimized = tuple(optimized)
        self.reward_agent = reward_agent
        self.prompter = prompter
        self.runner = runner
        self.judge = judge
        self.base = base
        self.seeds = list(seeds)
        self.out_dir = Path(out_dir)
        self.repair_attempts = repair_attempts
        self.callers = callers or {}
        self.history: List[Attempt] = []
        self.attempt_no = 0
        (self.out_dir / "steps").mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------------- one attempt
    def _attempt(self) -> Attempt:
        began = time.monotonic()
        usage_before = {role: _usage_of(c) for role, c in self.callers.items()}
        self.attempt_no += 1
        attempt = Attempt(step=self.attempt_no, candidate=Candidate(
            {}, optimized=self.optimized, reward_agent=self.reward_agent))

        try:
            candidate = self.prompter.propose(self.history)
        except Exception as exc:  # noqa: BLE001 — a dead prompter ends the run, loudly
            attempt.failure = f"prompter: {type(exc).__name__}: {exc}"
            attempt.prompter_meta = dict(getattr(self.prompter, "last_meta", {}) or {})
            return attempt
        attempt.candidate = candidate
        attempt.prompter_meta = dict(getattr(self.prompter, "last_meta", {}) or {})

        problems = candidate.validate(self.base)
        if problems:
            attempt.failure = "candidate: " + "; ".join(problems)
            return attempt

        artifacts = self.runner.run_seeds(candidate, self.seeds, step=self.attempt_no)
        attempt.errors = [a.error for a in artifacts if a.error]
        attempt.run_paths = [a.run_path for a in artifacts]
        if not any(a.ok for a in artifacts):
            attempt.failure = "rollouts: every seed failed — " + "; ".join(attempt.errors)[:400]
            return attempt
        attempt.ran = True

        # Seeds are judged concurrently: the preflight run spent 31 minutes judging one 16-turn
        # rollout (flash reasons at length on every call), which judged sequentially would make
        # judging three seeds the bulk of a step. The judge's own thread pool is per call, so
        # total concurrency is seeds × --judge-workers; flash-0731 on OpenRouter takes that.
        ok_arts = [a for a in artifacts if a.ok]
        with ThreadPoolExecutor(max_workers=len(ok_arts)) as pool:
            judged_all = list(pool.map(self._judge, ok_arts))
        per_seed: List[float] = []
        for art, judged in zip(ok_arts, judged_all):
            judged["seed"] = art.seed
            judged["run_path"] = art.run_path
            judged["reward_agent"] = self.reward_agent
            judged["optimized"] = list(self.optimized)
            attempt.judged.append(judged)
            per_seed.append(reward_mod.rollout_reward(judged))
            if art.run_dir:
                Path(art.run_dir).joinpath("judge.json").write_text(
                    json.dumps(judged, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
                try:
                    verdict_export.export(Path(art.run_dir), judged)
                except Exception as exc:  # noqa: BLE001 — the page is a convenience, never fatal
                    logger.warning("verdict export failed for %s: %s", art.run_dir, exc)
        attempt.reward = reward_mod.aggregate(per_seed)
        attempt.reward_detail = reward_mod.explain(attempt.judged, self.reward_agent)
        attempt.duration_s = round(time.monotonic() - began, 1)
        attempt.usage = {role: _usage_delta(usage_before.get(role, {}), _usage_of(c))
                         for role, c in self.callers.items()}
        return attempt

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
        """Rebuild ``history`` from ``steps/step_NNN.json`` and return the last opt_step done.

        A step file is written only after its attempt is complete (rollouts judged, reward in),
        and ``history.jsonl`` is appended right after, so killing the process between steps
        loses nothing, and a half-finished step leaves no file. The prompter sees exactly the
        trajectory it would have seen; ``best.json`` is recomputed from the same records."""
        files = sorted((self.out_dir / "steps").glob("step_*.json"))
        last_opt = 0
        for f in files:
            s = json.loads(f.read_text(encoding="utf-8"))
            a = Attempt(step=int(s["step"]), candidate=Candidate.from_dict(s["candidate"]),
                        ran=bool(s["ran"]), failure=str(s.get("failure") or ""),
                        reward=float(s.get("reward") or 0.0), judged=list(s.get("seeds") or []),
                        run_paths=[sd.get("run_path") for sd in (s.get("seeds") or [])],
                        errors=list(s.get("errors") or []),
                        reward_detail=dict(s.get("reward_detail") or {}),
                        prompter_meta=dict(s.get("prompter") or {}),
                        duration_s=float(s.get("duration_s") or 0), usage=dict(s.get("usage") or {}))
            a.extra_opt = (int(s.get("opt_step") or 0), int(s.get("repair") or 0))  # type: ignore[attr-defined]
            self.history.append(a)
            self.attempt_no = max(self.attempt_no, a.step)
            if a.ran:
                last_opt = max(last_opt, int(s.get("opt_step") or 0))
        if self.history:
            logger.info("resumed %d attempts from %s (last completed step %d)",
                        len(self.history), self.out_dir, last_opt)
            self._write_best()
        return last_opt

    # ------------------------------------------------------------------------------ driver
    def run(self, steps: int, *, start_at: int = 1) -> List[Attempt]:
        for opt_step in range(start_at, steps + 1):
            if (self.out_dir / "STOP").exists():
                logger.info("STOP file present — stopping before step %s; resumable", opt_step)
                break
            for repair in range(self.repair_attempts + 1):
                logger.info("step %s/%s%s", opt_step, steps, f" (repair {repair})" if repair else "")
                attempt = self._attempt()
                self.history.append(attempt)
                self._write_step(attempt, opt_step, repair)
                self._append_history(attempt, opt_step, repair)
                if attempt.ran:
                    logger.info("  reward %.2f (%s)", attempt.reward, attempt.candidate.digest())
                    self._write_best()
                    break
                if attempt.failure.startswith("prompter:"):
                    logger.error("  %s", attempt.failure)
                    return self.history
                logger.info("  did not run: %s", attempt.failure[:200])
            else:
                logger.info("  step %s exhausted its repair attempts", opt_step)
        return self.history

    # --------------------------------------------------------------------------- artifacts
    def _step_detail(self, a: Attempt, opt_step: int = 0, repair: int = 0) -> Dict[str, Any]:
        return {
            "schema": STEP_SCHEMA, "step": a.step, "opt_step": opt_step, "repair": repair,
            "ran": a.ran, "failure": a.failure, "reward": a.reward,
            "duration_s": a.duration_s, "usage": a.usage, "errors": a.errors,
            "candidate": a.candidate.to_dict(), "candidate_digest": a.candidate.digest(),
            "asks": a.candidate.all_asks(self.base) if a.candidate.asks else {},
            "prompter": a.prompter_meta,
            "reward_detail": a.reward_detail,
            "seeds": a.judged,
        }

    def _write_step(self, a: Attempt, opt_step: int, repair: int) -> None:
        a.extra_opt = (opt_step, repair)  # type: ignore[attr-defined]
        (self.out_dir / "steps" / f"step_{a.step:03d}.json").write_text(
            json.dumps(self._step_detail(a, opt_step, repair), indent=2, default=str), encoding="utf-8")

    def _append_history(self, a: Attempt, opt_step: int, repair: int) -> None:
        summ = {}
        for j in a.judged:
            for agent, s in (j.get("summary") or {}).items():
                slot = summ.setdefault(agent, {})
                for cat, n in s["categories"].items():
                    slot[cat] = slot.get(cat, 0) + n
        row = {
            "step": a.step, "opt_step": opt_step, "repair": repair, "ran": a.ran,
            "reward": a.reward, "digest": a.candidate.digest(), "failure": a.failure[:400],
            "rationale": str(a.prompter_meta.get("rationale") or "")[:400],
            "per_seed_reward": [r["reward"] for r in (a.reward_detail.get("per_seed") or [])],
            "majority_counts": summ,
            "duration_s": a.duration_s,
        }
        with open(self.out_dir / "history.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")

    def _write_best(self) -> None:
        ran = [a for a in self.history if a.ran]
        if ran:
            best = max(ran, key=lambda a: a.reward)
            opt, rep = getattr(best, "extra_opt", (0, 0))
            (self.out_dir / "best.json").write_text(
                json.dumps(self._step_detail(best, opt, rep), indent=2, default=str), encoding="utf-8")

    def write_metadata(self, extra: Dict[str, Any]) -> None:
        meta = {
            "fixture": {"path": str(self.runner.fixture_path), "version": self.base.version,
                        "sha": self.base.sha},
            "seeds": self.seeds,
            "optimized": list(self.optimized), "reward_agent": self.reward_agent,
            "fixed_ask": self.runner.settings.get("ask") or fixed_ask_for(self.base),
            "reward": {"name": reward_mod.NAME, "weights": reward_mod.WEIGHTS,
                       "counted_intents": list(reward_mod.COUNTED_INTENTS)},
            "judge_version": JUDGE_VERSION,
            "repair_attempts": self.repair_attempts,
            "gates": [],
            "step_schema": STEP_SCHEMA,
            **extra,
        }
        (self.out_dir / "metadata.json").write_text(json.dumps(meta, indent=2, default=str),
                                                    encoding="utf-8")
        (self.out_dir / "prompter_system.md").write_text(self.prompter.system_prompt, encoding="utf-8")


# -------------------------------------------------------------------------------------- CLI
def main() -> None:
    parser = argparse.ArgumentParser(description="agent3 — two-ask optimization over agent1 v17.")
    parser.add_argument("--steps", type=int, default=5)
    parser.add_argument("--seeds", default="1,2,3")
    parser.add_argument("--fixture", default=DEFAULT_FIXTURE)
    parser.add_argument("--config", default=None, help="YAML config for the target model")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--optimize", default=",".join(DEFAULT_OPTIMIZED),
                        help="comma-separated principals whose asks the prompter writes")
    parser.add_argument("--reward-agent", default=DEFAULT_REWARD_AGENT,
                        help="the principal whose turns reward_v1 reads")
    parser.add_argument("--repair-attempts", type=int, default=3)
    parser.add_argument("--parallel-seeds", type=int, default=3)
    parser.add_argument("--prompter-model", default=DEFAULT_PROMPTER)
    parser.add_argument("--prompter-max-tokens", type=int, default=24000)
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE)
    parser.add_argument("--judge-temperature", type=float, default=0.0,
                        help="per-call temperature for the three votes (0.0 matches the jv7 "
                             "replicate files; the model is not deterministic at 0 anyway)")
    parser.add_argument("--judge-providers", default="DeepInfra,Fireworks,Together,Novita",
                        help="OpenRouter provider order for the judge, no fallbacks; '' = unpinned")
    parser.add_argument("--judge-retries", type=int, default=1,
                        help="re-asks per judge call on an unparseable reply (the HTTP client "
                             "retries transport errors on its own: OPENROUTER_MAX_RETRIES)")
    parser.add_argument("--judge-workers", type=int, default=6,
                        help="concurrent judge calls PER SEED (seeds are judged in parallel)")
    parser.add_argument("--judge-max-tokens", type=int, default=16000)
    parser.add_argument("--judge-roles", default="",
                        help="comma-separated workspace titles to judge (default: every principal)")
    parser.add_argument("--resume", action="store_true",
                        help="continue a run in --out-dir from its step files; --steps is the "
                             "TOTAL step count, not the number still to run")
    parser.add_argument("--offline", action="store_true",
                        help="scripted target + keyword stub judge — checks the wiring only")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    seeds = [int(s) for s in str(args.seeds).split(",") if s.strip()]
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
        if args.fixture != meta.get("fixture", {}).get("path", args.fixture):
            parser.error(f"{out_dir} was run on {meta['fixture']['path']}, not {args.fixture}")
    if (problems := check_roles(base, optimized, reward_agent)):
        parser.error("; ".join(problems))

    settings: Dict[str, Any] = {}
    make_client = None
    if args.config:
        import yaml

        from experiments.agent1 import run as agent1_run

        with open(args.config, "r", encoding="utf-8") as fh:
            settings = agent1_run.resolve_settings(yaml.safe_load(fh) or {})
    # The month comes from the fixture's clock, as agent1's own prompts now do; a config `ask` wins.
    fixed_ask = str(settings.get("ask") or fixed_ask_for(base))

    callers: Dict[str, Any] = {}
    if args.offline:
        from experiments.agent1.smoke import ScriptedClient

        from experiments.agent3.smoke import StubJudgeCaller, offline_candidates, offline_scripts

        prompter: Any = ScriptedPrompter(base, offline_candidates(fixed_ask, optimized, reward_agent))
        judge: Any = MajorityJudge(StubJudgeCaller(reward_agent), workers=1)
        make_client = lambda _s: (lambda name: ScriptedClient(  # noqa: E731
            offline_scripts(Workspace.load(args.fixture), name, reward_agent)))
        settings = {"max_rounds": 3, "max_conversation_steps": 8, "turn_jitter": 0,
                    "harness": "full", "confidentiality": "inference",
                    "discussion_norms": "self_and_others",
                    "calendar_example_day": base.now.strftime("%Y-%m-%d")}
    else:
        if not args.config:
            parser.error("--config is required for a live run")
        # deepseek-v4-pro-0813 spent ~7.5k reasoning tokens on the cold-start call and overran
        # sj4's 8192 default twice before answering; the budget has to hold reasoning + JSON.
        prompter_caller = make_prompter_caller(model=args.prompter_model,
                                               max_tokens=args.prompter_max_tokens)
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
        callers = {"prompter": prompter_caller, "judge": judge_caller}
        prompter = Prompter(prompter_caller, base, fixed_ask=fixed_ask,
                            optimized=optimized, reward_agent=reward_agent)
        roles = [r for r in args.judge_roles.split(",") if r.strip()] or None
        judge = MajorityJudge(judge_caller, workers=args.judge_workers, roles=roles,
                              retries=args.judge_retries)

    runner = TargetRunner(args.fixture, settings, out_dir / "runs", make_client=make_client,
                          parallel_seeds=args.parallel_seeds)
    loop = Loop(prompter=prompter, runner=runner, judge=judge, base=base, seeds=seeds,
                out_dir=out_dir, repair_attempts=args.repair_attempts, callers=callers,
                optimized=optimized, reward_agent=reward_agent)
    start_at = 1
    if args.resume:
        start_at = loop.resume() + 1
    elif (out_dir / "history.jsonl").exists():
        parser.error(f"{out_dir} already holds a run; pass --resume to continue it or pick a new --out-dir")
    loop.write_metadata({
        "offline": bool(args.offline),
        "target_model": settings.get("model_name") or ("scripted" if args.offline else ""),
        "config": args.config,
        "prompter_model": args.prompter_model if not args.offline else "scripted",
        "judge_model": args.judge_model if not args.offline else "stub",
        "judge_temperature": args.judge_temperature,
        "judge_replicates": judge.replicates,
        "judge_roles": args.judge_roles or "(all principals)",
        "judge_providers": args.judge_providers if not args.offline else "",
        "judge_retries": args.judge_retries,
        "judge_workers": args.judge_workers,
        "openrouter_max_retries": __import__("os").getenv("OPENROUTER_MAX_RETRIES"),
        "parallel_seeds": args.parallel_seeds,
    })

    history = loop.run(args.steps, start_at=start_at)
    ran = [a for a in history if a.ran]
    print(f"\n{len(ran)}/{len(history)} attempts ran; wrote {out_dir}")
    if ran:
        best = max(ran, key=lambda a: a.reward)
        print(f"best: step {best.step}, reward {best.reward:.2f} ({best.candidate.digest()})")


if __name__ == "__main__":
    main()
