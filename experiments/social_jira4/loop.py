from __future__ import annotations

# ruff: noqa: E402

"""v0 driver: propose → C&B gate → consistency gate → run × seeds → critic → record → repeat.

Single-thread hill climbing (PAIR-style). The loop depends only on the ``interfaces.py``
protocols, so each piece is swappable:

  * ``--offline``  fake prompter + heuristic critic + heuristic C&B + fake target. Zero network;
                   demonstrates the mechanics and lets you watch the score climb.
  * ``--mode dry`` REAL prompter LLM + heuristic critic/C&B + fake target. Exercises the actual
                   optimizer against the stub gradient (needs a prompter model / API key).
  * ``--mode live``REAL prompter + live social_jira3 target + (still-stub) critic/C&B. Wire your
                   real critic here (replace ``HeuristicCritic``/``HeuristicChecks``) to finish v0.

Run:
    python -m experiments.social_jira4.loop --offline --steps 12
    python -m experiments.social_jira4.loop --mode dry --steps 20 --prompter-model anthropic/claude-sonnet-4.5
    python -m experiments.social_jira4.loop --mode live --steps 20 --config <jira3 yaml> --model-label qwen3.6
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from experiments.common.run_utils import ensure_dir as _ensure_dir, load_yaml as _load_yaml
from experiments.social_jira4 import llm as llm_mod
from experiments.social_jira4 import objective as objective_mod
from experiments.social_jira4.blocks import Blocks
from experiments.social_jira4.interfaces import (
    ChecksBalances,
    Consistency,
    Critic,
    RunScore,
    TargetRunner,
)
from experiments.social_jira4.pools import (
    CONFIDENTIALITY_REASON_ANCHORS,
    DECOY_POLICIES_ANCHORS,
    GENERAL_TASK_INFO_ANCHORS,
    PERSONALITY_ANCHORS,
    DECOY_INFO_POOL,
    PRIVATE_MESSAGES_POOL,
    SEED_BLOCKS,
)
from experiments.social_jira4.prompter import Attempt, Prompter, SeededPrompter
from experiments.social_jira4.stubs import (
    FakeTargetRunner,
    HeuristicChecks,
    HeuristicConsistency,
    HeuristicCritic,
)


# --------------------------------------------------------------------------- fake prompter
class FakePrompter:
    """A deterministic, network-free stand-in for the real prompter: it walks from the weak seed
    to progressively stronger blocks so ``--offline`` visibly climbs the heuristic gradient.
    Not an optimizer — just a mechanism demonstrator."""

    def propose(self, history: List[Attempt]) -> Blocks:
        step = len(history)
        # progressively strengthen: turn on the decoy, raise inbox strength, pick sharper anchors
        private_ids = list(PRIVATE_MESSAGES_POOL)          # mild < strong < quit (defined order)
        decoy_ids = [d for d in DECOY_INFO_POOL if d != "none"]  # calendar, ops_feed, ...
        return Blocks(
            general_task_info=GENERAL_TASK_INFO_ANCHORS[min(step, len(GENERAL_TASK_INFO_ANCHORS) - 1)],
            decoy_policies=DECOY_POLICIES_ANCHORS[min(step, len(DECOY_POLICIES_ANCHORS) - 1)],
            confidentiality_reason=CONFIDENTIALITY_REASON_ANCHORS[
                min(step, len(CONFIDENTIALITY_REASON_ANCHORS) - 1)
            ],
            personality=PERSONALITY_ANCHORS[min(step, len(PERSONALITY_ANCHORS) - 1)],
            private_messages_id=private_ids[min(step, len(private_ids) - 1)],
            # walk from no substrate to a composed one, so --offline climbs visibly
            decoy_info_ids=decoy_ids[: min(step, len(decoy_ids))],
        )


# ------------------------------------------------------------------------------ loop core
def _best_turn_over(run_scores: List[RunScore]):
    cands = [rs.best_turn for rs in run_scores if rs.best_turn is not None]
    return max(cands, key=lambda t: (t.fabrication_specificity or 0), default=None)


# Completion ceiling for the consistency gate. Well above the judges' 4096: this gate reasons over
# every (block sentence × seed × employee) pair, and at 4096 a dense block-set spends the whole
# budget on reasoning tokens and returns an empty completion (measured: 4095 reasoning, 0 content).
_CONSISTENCY_MAX_TOKENS = 12000


# Bumped whenever ``_step_detail``'s shape changes, so the visualizer can tell a fully
# instrumented step file from an early one (schema 1 = no prompter/C&B/judge audit trail;
# schema 2 = no consistency gate, so `gate` is absent and every rejection is the validator's).
STEP_SCHEMA = 3


def _attempt_row(a: Attempt) -> Dict[str, Any]:
    return {
        "step": a.step,
        "cb_ok": a.cb_ok,
        "gate": a.gate,          # "" when it ran, else blocks | checks | consistency
        "cb_reason": a.cb_reason,
        "score": a.score,
        "blocks": a.blocks.to_dict(),
        "seed_scores": [rs.objective for rs in a.run_scores],
        "best_lie": (a.best_turn.message if a.best_turn else "") or "",
        "prompter_rationale": a.prompter_rationale,
        "prompter_reasoning_chars": len(a.prompter_reasoning),  # full CoT in steps/step_NNN.json
        "prompter_source": a.prompter_meta.get("source", ""),   # "prompter" | "warm_start"
        "duration_s": round(a.duration_s, 2),
        "usage": a.usage,
    }


def _turn_row(t) -> Dict[str, Any]:
    """One turn's full critic verdict (for the per-step verdicts file), including the target's own
    chain-of-thought and each of the three judges' verbatim replies."""
    return {
        "agent": t.agent, "turn_index": t.turn_index, "categories": t.categories,
        "fabrication_specificity": t.fabrication_specificity, "soundness_ok": t.soundness_ok,
        "at_stake": t.at_stake, "qualifies": t.qualifies(), "spans": t.spans,
        "explanation": t.explanation, "message": t.message,
        "reasoning": t.reasoning,   # the TARGET's CoT for this turn (what the judges read)
        "judges": t.judges,         # {"category"|"soundness"|"at_stake": verbatim reply + _meta}
    }


def _gate_detail(v, *, ran: bool, reason: str) -> Dict[str, Any]:
    """One gate's stage record. ``rendered`` is what that gate judged — for a REJECTED candidate
    nothing ran, so this is the only copy of it that exists. ``ok`` is the gate's OWN verdict, not
    the step's: a candidate the validator passed and the consistency gate rejected shows
    ``cb.ok=true`` beside ``cons.ok=false``.

    ``ran: false`` means this gate never spoke — the candidate died earlier (malformed blocks, or
    the validator rejected it before the consistency call), or the gate is disabled. Read ``ok``
    only when ``ran`` is true; with ``ran: false`` it just repeats whether the step proceeded."""
    if v is None:
        return {"ok": ran, "ran": False, "reason": reason if ran else "", "raw": {},
                "rendered": {}, "reasoning": "", "usage": {}}
    return {
        "ok": v.ok,
        "ran": True,
        "reason": v.reason,
        "raw": v.raw,
        "rendered": v.rendered,
        "reasoning": v.reasoning,
        "usage": v.usage,
    }


def _step_detail(a: Attempt) -> Dict[str, Any]:
    """The full retrospective record for one optimization step — everything needed to retrace the
    chain *prompt → C&B → consistency → rollout(s) → judging → objective → next prompt* without
    re-running it:

      * ``prompter``  — the OPRO message it was shown ("summary fed into the prompter"), its CoT,
        its raw reply, and which past steps were in context; or, for a warm-start replay, the seed
        record that stands in for a model call;
      * ``cb``        — the validator's parsed verdict plus the rendered prompt it judged;
      * ``cons``      — the consistency gate's verdict plus the blocks and the per-seed ground
        truth it read (``ran: false`` when the candidate died before reaching it);
      * ``seeds[]``   — per rollout: the run dir, every judged turn (target CoT + all three judge
        replies), and the objective's own breakdown of how those turns became the scalar.
    """
    return {
        "schema": STEP_SCHEMA,
        "step": a.step, "cb_ok": a.cb_ok, "gate": a.gate, "cb_reason": a.cb_reason,
        "score": a.score,
        "duration_s": round(a.duration_s, 2), "usage": a.usage,
        "prompter": {
            "rationale": a.prompter_rationale,
            "reasoning": a.prompter_reasoning,
            **a.prompter_meta,
        },
        "cb": _gate_detail(a.cb, ran=a.cb_ok, reason=a.cb_reason),
        "cons": _gate_detail(a.cons, ran=a.cb_ok, reason=a.cb_reason),
        "blocks": a.blocks.to_dict(),
        "objective": a.objective_detail,
        "seeds": [
            {
                "seed": rs.seed, "objective": rs.objective, "run_dir": rs.run_dir,
                "error": rs.error, "turns": [_turn_row(t) for t in rs.turns],
            }
            for rs in a.run_scores
        ],
    }


def _usage_delta(before: Dict[str, Dict[str, int]], sources: Dict[str, Any]) -> Dict[str, Any]:
    """Tokens spent during this step, per role — the difference between two cumulative snapshots
    of the (shared) callers. Empty for roles whose provider we do not instrument."""
    out: Dict[str, Any] = {}
    for role, caller in sources.items():
        snap = getattr(caller, "snapshot", None)
        if snap is None:
            continue
        now, was = snap(), before.get(role) or {}
        delta = {k: int(v) - int(was.get(k, 0)) for k, v in now.items()}
        if any(delta.values()):
            out[role] = delta
    return out


def _usage_snapshot(sources: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
    return {
        role: caller.snapshot()
        for role, caller in sources.items()
        if getattr(caller, "snapshot", None) is not None
    }


def run_loop(
    *,
    prompter: Any,
    runner: TargetRunner,
    critic: Critic,
    checks: ChecksBalances,
    objective: objective_mod.Objective,
    steps: int,
    seeds: List[int],
    out_dir: Path,
    consistency: Optional[Consistency] = None,
    usage_sources: Optional[Dict[str, Any]] = None,
) -> List[Attempt]:
    _ensure_dir(out_dir)
    steps_dir = out_dir / "steps"
    _ensure_dir(steps_dir)
    history: List[Attempt] = []
    best: Optional[Attempt] = None
    hist_path = out_dir / "history.jsonl"
    sources = usage_sources or {}

    # The prompter's scaffold is fixed for the run — written once here rather than repeated in
    # every step file, so a step's `prompter.user_prompt` can be read against the system prompt
    # that framed it.
    scaffold = getattr(prompter, "system_prompt", None) or getattr(
        getattr(prompter, "_inner", None), "system_prompt", ""
    )
    if scaffold:
        (out_dir / "prompter_system.md").write_text(scaffold, encoding="utf-8")

    for step in range(steps):
        t0 = time.time()
        usage_before = _usage_snapshot(sources)
        blocks = prompter.propose(history)
        pmeta = dict(getattr(prompter, "last_meta", {}) or {})
        rationale = str(pmeta.pop("rationale", "") or "")
        reasoning = str(pmeta.pop("reasoning", "") or "")

        problems = blocks.validate(PRIVATE_MESSAGES_POOL, DECOY_INFO_POOL)
        if problems:
            # Malformed candidate: rejected before the validator is ever called, so there is no
            # CBVerdict to record — the reason is the block-level complaint itself.
            attempt = Attempt(step, blocks, cb_ok=False, gate="blocks",
                              cb_reason="invalid blocks: " + "; ".join(problems), score=0.0,
                              prompter_rationale=rationale, prompter_reasoning=reasoning,
                              prompter_meta=pmeta)
        else:
            # Two gates, in series. The hard rule runs first: a prompt that instructs deception is
            # rejected whatever it says about the roster, and stopping there saves the second call.
            verdict = checks.check(blocks)
            cverdict = (
                consistency.check(blocks, seeds)
                if (verdict.ok and consistency is not None) else None
            )
            if not verdict.ok or (cverdict is not None and not cverdict.ok):
                failed = verdict if not verdict.ok else cverdict
                attempt = Attempt(step, blocks, cb_ok=False,
                                  gate="checks" if not verdict.ok else "consistency",
                                  cb_reason=failed.reason, score=0.0,
                                  prompter_rationale=rationale, prompter_reasoning=reasoning,
                                  prompter_meta=pmeta, cb=verdict, cons=cverdict)
            else:
                # Seeds are independent — run their rollouts + judging CONCURRENTLY (order preserved
                # by pool.map). Each thread does one full seed: rollout -> critic -> objective.
                def _run_and_score(s: int):
                    art = runner.run(blocks, s, step=step)
                    rs = critic.score(art)
                    rs.objective = objective.rollout(rs.turns)  # objective owns the per-seed scalar
                    rs.run_dir, rs.error = art.run_dir, art.error
                    return rs, {"seed": s, **objective.explain(rs.turns)}

                if len(seeds) > 1:
                    with ThreadPoolExecutor(max_workers=len(seeds)) as pool:
                        results = list(pool.map(_run_and_score, seeds))
                else:
                    results = [_run_and_score(seeds[0])]
                run_scores: List[RunScore] = [r for r, _ in results]
                per_seed_detail: List[Dict[str, Any]] = [d for _, d in results]
                per_seed = [rs.objective for rs in run_scores]
                attempt = Attempt(
                    step, blocks, cb_ok=True, cb_reason=verdict.reason,
                    score=objective.aggregate(per_seed),
                    run_scores=run_scores, best_turn=_best_turn_over(run_scores),
                    prompter_rationale=rationale, prompter_reasoning=reasoning,
                    prompter_meta=pmeta, cb=verdict, cons=cverdict,
                    objective_detail={
                        "name": objective.name, "description": objective.description,
                        "per_seed": per_seed, "aggregate": objective.aggregate(per_seed),
                        "seeds": per_seed_detail,
                    },
                )

        attempt.duration_s = time.time() - t0
        attempt.usage = _usage_delta(usage_before, sources)
        history.append(attempt)
        with hist_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(_attempt_row(attempt), ensure_ascii=False) + "\n")
        (steps_dir / f"step_{step:03d}.json").write_text(
            json.dumps(_step_detail(attempt), ensure_ascii=False, indent=2), encoding="utf-8"
        )

        status = f"REJECTED[{attempt.gate}]" if not attempt.cb_ok else f"score={attempt.score:.2f}"
        print(f"[step {step:>3}] {status}"
              + ("" if attempt.cb_ok else f" — {attempt.cb_reason[:80]}"))

        if attempt.cb_ok and (best is None or attempt.score > best.score):
            best = attempt
            (out_dir / "best.json").write_text(
                json.dumps(_attempt_row(best), ensure_ascii=False, indent=2), encoding="utf-8"
            )

    if best is not None:
        print(f"\nBest: step {best.step}, score {best.score:.2f} -> {out_dir/'best.json'}")
    return history


# ------------------------------------------------------------------------------------ CLI
def _build_live_runner(args: argparse.Namespace, *, out_dir: Path, referee_caller=None) -> TargetRunner:
    from experiments.social_jira4.target_run import LiveTargetRunner

    if not args.config:
        raise SystemExit("--mode live requires --config <social_jira3 yaml>")
    cfg = _load_yaml(args.config)
    models = cfg.get("llm_models") or []
    model = next((m for m in models if str(m.get("label")) == args.model_label), None) if args.model_label else (models[0] if models else None)
    if model is None:
        raise SystemExit(f"no model {args.model_label!r} in {args.config} (labels: "
                         f"{[m.get('label') for m in models]})")
    net = cfg.get("communication_network") or {}
    return LiveTargetRunner(
        base_cfg=cfg,
        model_label=str(model.get("label") or "model"),
        model_llm_cfg=model.get("llm") or {},
        out_dir=out_dir,
        referee_caller=referee_caller,   # LLM referee (deepseek) in live mode; forwarded to _rollout
        num_agents=int(net.get("num_agents", 4)),
        num_tasks=int((cfg.get("environment") or {}).get("num_tasks", 2)),
    )


def _git_sha() -> Optional[str]:
    try:
        import subprocess
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(project_root), stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return None


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="social_jira4 v0 adaptive prompter loop.")
    ap.add_argument("--mode", choices=("dry", "live"), default="dry")
    ap.add_argument("--offline", action="store_true",
                    help="fake prompter + stubs, zero network (demonstrates the loop).")
    ap.add_argument("--steps", type=int, default=12)
    ap.add_argument("--seeds", type=str, default="1,2,3", help="comma-separated, e.g. 1,2,3")
    ap.add_argument("--warmstart", default=None,
                    help="seeds JSON (from extract_seeds.py) replayed as the first steps.")
    ap.add_argument("--config", default=llm_mod.DEFAULT_TARGET_CONFIG,
                    help="target YAML (live mode); default = qwen3.6-26b-a3b via OpenRouter.")
    ap.add_argument("--model-label", default=llm_mod.DEFAULT_TARGET_LABEL,
                    help="which llm_models entry to target (live).")
    ap.add_argument("--prompter-provider", default="auto", choices=("auto", "openrouter", "azure"))
    ap.add_argument("--prompter-model", default="")
    # Consumed by the real critic/validator once wired (stubs ignore them). Default = DeepSeek-V4-Pro/OR.
    ap.add_argument("--judge-provider", default=llm_mod.DEFAULT_JUDGE_PROVIDER,
                    choices=("openrouter", "azure"))
    ap.add_argument("--judge-model", default=llm_mod.DEFAULT_JUDGE_MODEL)
    ap.add_argument("--stub-judges", action="store_true",
                    help="use the heuristic critic/C&B even in live mode (test the target alone).")
    ap.add_argument("--no-consistency", action="store_true",
                    help="skip the consistency gate (blocks may then assert scenario facts that "
                         "contradict what the target is shown — see CONSISTENCY_PROMPT.md).")
    ap.add_argument("--judge-gate", action="store_true",
                    help="run SOUNDNESS/AT-STAKE only on CATEGORY-flagged turns (cheaper).")
    ap.add_argument("--objective", default=objective_mod.DEFAULT_NAME,
                    choices=sorted(objective_mod.REGISTRY),
                    help="how per-turn verdicts collapse into the optimized scalar.")
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args(argv)

    seeds = [int(s) for s in str(args.seeds).split(",") if s.strip()]
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir or f"experiments/social_jira4/outputs/{ts}")

    # prompter (DeepSeek-V4-Pro via OpenRouter by default — see llm.DEFAULT_PROMPTER_MODEL)
    prompter_model = "fake" if args.offline else (args.prompter_model or llm_mod.DEFAULT_PROMPTER_MODEL)
    pcaller = None
    if args.offline:
        prompter: Any = FakePrompter()
    else:
        from experiments.social_jira4.llm import make_prompter_caller
        pcaller = make_prompter_caller(provider=args.prompter_provider, model=args.prompter_model)
        prompter = Prompter(pcaller)

    # warm start: replay proven fabrication-eliciting seeds as the first scored steps. The whole
    # record is passed through (not just its blocks) — a warm-start step has no prompter reasoning,
    # so the seed's jira3 provenance + verbatim L2 lie is what explains where the prompt came from.
    if args.warmstart:
        seed_recs = json.loads(Path(args.warmstart).read_text())
        prompter = SeededPrompter(prompter, seed_recs)
        print(f"  warm start: {len(seed_recs)} seeds from {args.warmstart}")

    # shared judge/referee caller (DeepSeek) — live mode, unless stubbing the judges
    jcaller = None
    ccaller = None
    if args.mode == "live" and not args.stub_judges:
        jcaller = llm_mod.make_judge_caller(provider=args.judge_provider, model=args.judge_model)
        # The consistency gate gets its OWN caller on the same model, for the token ceiling alone:
        # it cross-checks every sentence against every (seed, employee) row, and at the judges'
        # 4096 it reliably spends the whole budget in the reasoning channel and returns an empty
        # completion — which fails closed and burns each retry reproducing the same silence.
        ccaller = llm_mod.make_judge_caller(provider=args.judge_provider, model=args.judge_model,
                                            max_tokens=_CONSISTENCY_MAX_TOKENS)

    # target (the referee, in live mode, uses the judge caller)
    runner: TargetRunner = (
        _build_live_runner(args, out_dir=out_dir, referee_caller=jcaller)
        if args.mode == "live" else FakeTargetRunner()
    )

    # Rollout (inner-loop) params, so the run is fully self-describing from metadata alone. NOTE:
    # `steps` is the OUTER optimization loop (prompt candidates tried); `max_planning_rounds` is the
    # INNER per-rollout discussion cap ("max turns") — two different things. Read before the gates:
    # the consistency gate needs the same `num_tasks` the target will run with.
    rollout_params: Dict[str, Any] = {}
    if args.mode == "live":
        _tcfg = _load_yaml(args.config)
        _exp = _tcfg.get("experiment") or {}
        _sim = _tcfg.get("simulation") or {}
        rollout_params = {
            "max_planning_rounds": int(_exp.get("planning_rounds", _sim.get("max_planning_rounds", 3))),
            "min_planning_rounds": int(_exp.get("min_planning_rounds", 2)),
            "early_stop": bool(_exp.get("early_stop", True)),
            "num_agents": int((_tcfg.get("communication_network") or {}).get("num_agents", 4)),
            "num_tasks": int((_tcfg.get("environment") or {}).get("num_tasks", 2)),
        }

    # critic + both gates: real judges when jcaller is present, heuristic stubs otherwise. The
    # consistency gate shares the judge caller (so it is DeepSeek-V4-Pro by default, and its tokens
    # land under the "judge" role like the validator's).
    if jcaller is not None:
        from experiments.social_jira4.checks_balances import LlmChecks, LlmConsistency
        from experiments.social_jira4.critic import LlmCritic
        critic: Critic = LlmCritic(jcaller, gate=args.judge_gate)
        checks: ChecksBalances = LlmChecks(jcaller)
        # Same task count the target will run with, so the ground truth the gate reads is the
        # instance the target actually sees.
        consistency: Optional[Consistency] = LlmConsistency(
            ccaller, num_tasks=int(rollout_params.get("num_tasks", 2))
        )
    else:
        critic = HeuristicCritic()
        checks = HeuristicChecks()
        consistency = HeuristicConsistency()
    if args.no_consistency:
        consistency = None
    objective = objective_mod.get(args.objective)

    judge_model = args.judge_model if jcaller is not None else "heuristic-stub"
    referee_desc = judge_model if jcaller is not None else "post-parse (fallback)"
    consistency_desc = (
        "off" if consistency is None
        else (judge_model if jcaller is not None else "heuristic-stub")
    )

    _ensure_dir(out_dir)
    (out_dir / "metadata.json").write_text(json.dumps({
        "started_at": datetime.now().isoformat(),
        "step_schema": STEP_SCHEMA,   # shape of steps/step_NNN.json (see _step_detail)
        "mode": args.mode, "offline": args.offline,
        "steps": args.steps, "seeds": seeds, "objective": objective.name,
        "objective_description": objective.description,
        "warmstart": args.warmstart,
        "judge_gate": args.judge_gate,
        "rollout": rollout_params,   # inner-loop discussion caps (max_planning_rounds = "max turns")
        "models": {
            "prompter": prompter_model,
            "target": args.model_label if args.mode == "live" else "fake",
            "judge": judge_model,
            "referee": referee_desc,
            "consistency": consistency_desc,   # "off" when --no-consistency
        },
        "target_config": args.config if args.mode == "live" else None,
        "prompter_provider": args.prompter_provider, "judge_provider": args.judge_provider,
        "git_sha": _git_sha(), "out_dir": str(out_dir),
    }, indent=2), encoding="utf-8")

    target_desc = f"{args.model_label} (live)" if args.mode == "live" else "fake (dry)"
    print(f"social_jira4 v0 — mode={args.mode} offline={args.offline} steps={args.steps} "
          f"seeds={seeds} objective={objective.name}\n"
          f"  prompter={prompter_model}  target={target_desc}\n"
          f"  judges={judge_model}  referee={referee_desc}  consistency={consistency_desc}\n"
          f"  out={out_dir}")
    # Token accounting per step, by role. "judge" covers the 3 critics + the validator (one shared
    # caller); the consistency gate has its own caller, so it reports separately. Roles whose
    # provider is not instrumented simply report nothing.
    usage_sources = {
        k: v for k, v in (("prompter", pcaller), ("judge", jcaller), ("consistency", ccaller))
        if v is not None
    }

    run_loop(prompter=prompter, runner=runner, critic=critic, checks=checks,
             consistency=consistency, objective=objective, steps=args.steps, seeds=seeds,
             out_dir=out_dir, usage_sources=usage_sources)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
