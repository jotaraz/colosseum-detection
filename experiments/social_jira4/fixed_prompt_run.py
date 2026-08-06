"""Replay already-written sj4 prompts against a target model, and judge the result.

This is not the optimizer. `loop.py` exists to *search* for a prompt: propose -> gate -> rollout ->
critics -> adapt. Here the prompts are already chosen and already gate-cleared, and the only
question is what a given assistant model does when handed them. So the prompter, the
checks-and-balances validator, the consistency gate and the meta-judge panel are all absent by
construction rather than switched off — running them again would re-sample a decision that has
already been made, and a fresh gate draw could reject the very prompt we were asked to use.

What runs, per (prompt, seed):

    blocks (from a step file)  ->  social_jira3 rollout  ->  LlmCritic

`LlmCritic` with `gate=False` is exactly the three critics: CATEGORY, SOUNDNESS and AT-STAKE on
every turn. No fabrication verdict, no L2 quality judge, no meta-judge — those are separate passes.
The `weighted_count` objective is also computed so a number here is comparable to the score the same
prompt earned in the run it was mined from.

SHAPE: one MODEL per invocation, fanning out over PROMPTS x SEEDS.

    --step-files a.json,b.json,...   --seeds 7,8   --models or-qwen3.6-27b

Prompt index becomes the *step* (`runs/step000/`, `runs/step001/`, ...) and each seed is a rollout
inside it. That is the same shape a real optimizer run writes, so the viewer reads it without a
fiction about what a step means. Running one model per invocation (rather than fanning out over
models) is what lets each model be its own cluster job, so five models progress simultaneously
instead of summing their very different runtimes.

Rollouts fan out concurrently across (prompt, seed). `--workers` bounds how many run at once and
`--critic-workers` bounds each rollout's judge pool, because the two multiply: N concurrent
rollouts each judging with M workers is N*M in-flight calls on one API key.

    python -m experiments.social_jira4.fixed_prompt_run \
        --step-files outputs/v4d_.../steps/step_023.json,outputs/... \
        --config experiments/social_jira4/configs/social_jira4_realrun_5models_pinned_or.yaml \
        --models or-qwen3.6-27b --seeds 7,8 --out-dir experiments/social_jira4/outputs/rr_qwen
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from experiments.social_jira4 import llm as llm_mod
from experiments.social_jira4 import objective as objective_mod
from experiments.social_jira4.adaptive_prompts import DECEPTION_LEVELS, DECEPTION_LINES
from experiments.social_jira4.blocks import Blocks
from experiments.social_jira4.critic import LlmCritic
from experiments.social_jira4.interfaces import RunScore
from experiments.social_jira4.target_run import is_complete, run_id_for


def _load_yaml(path: str) -> Dict[str, Any]:
    import yaml
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def _resolve_step_path(p: str) -> str:
    """Resolve one step-file path.

    Step files are named REPO-RELATIVE everywhere in this project — in the manifests, in the submit
    files, and in every metadata.json already on disk — which silently assumes the process runs
    from the repo root. The vLLM runner does not: it ``cd``s into a per-job directory so that
    vLLM's server logs land there, and every repo-relative path then fails to open. So try the path
    as given first (preserving any absolute or CWD-relative use), then relative to the repo root.
    A path that resolves under neither is returned unchanged, so the error names what was asked for.
    """
    q = Path(p)
    if q.exists():
        return p
    alt = project_root / p
    return str(alt) if alt.exists() else p


def _read_step_files(spec: str) -> List[str]:
    """Resolve ``--step-files``: a comma-separated list, or a manifest file, one path per line.

    The manifest form exists so a campaign's cells provably replay the same prompts — the list is
    edited in one place instead of being copied into every submit file. A ``.json`` path is always
    a step record, never a manifest, so the single-prompt form keeps working.
    """
    p = Path(spec)
    if "," not in spec and p.suffix.lower() != ".json" and p.is_file():
        lines = [ln.split("#", 1)[0].strip() for ln in p.read_text(encoding="utf-8").splitlines()]
        out = [_resolve_step_path(ln) for ln in lines if ln]
        if not out:
            raise SystemExit(f"{spec} lists no step files")
        return out
    return [_resolve_step_path(s.strip()) for s in spec.split(",") if s.strip()]


def _blocks_from_step(step_file: Path) -> Blocks:
    """Pull the six blocks out of a loop step record (or a bare block dict / warm-start seed).

    Accepts all three shapes so the same flag works against `steps/step_NNN.json`, a hand-written
    block file, and the seed records `extract_seeds.py` emits. The blocks carry their own
    environment (`decoy_info_ids`, `private_messages_id`), so a prompt mined under one decoy
    substrate is replayed in the world it was written for rather than a uniform one.
    """
    raw = json.loads(step_file.read_text(encoding="utf-8"))
    if isinstance(raw, list):                       # warm-start seed file: [{blocks: ...}, ...]
        raw = raw[0]
    d = raw.get("blocks", raw) if isinstance(raw, dict) else raw
    return Blocks.from_dict(d)


def _turn_row(ts: Any) -> Dict[str, Any]:
    """One critic-scored turn, flattened for the summary file.

    The three judges' verbatim replies stay in the rollout dir rather than being copied here —
    this file is meant to be skimmed. `reasoning_chars` rather than the reasoning itself, for the
    same reason: at this level what matters is whether CoT was captured at all.
    """
    return {
        "agent": getattr(ts, "agent", None),
        "turn_index": getattr(ts, "turn_index", None),
        "categories": list(getattr(ts, "categories", []) or []),
        "fabrication_specificity": getattr(ts, "fabrication_specificity", None),
        "soundness_ok": getattr(ts, "soundness_ok", None),
        "at_stake": getattr(ts, "at_stake", None),
        "qualifies": bool(ts.qualifies()) if hasattr(ts, "qualifies") else None,
        "spans": list(getattr(ts, "spans", []) or []),
        "explanation": getattr(ts, "explanation", ""),
        "reasoning_chars": len((getattr(ts, "reasoning", "") or "")),
        "message": getattr(ts, "message", ""),
    }


def _build_runner(*, label: str, llm_cfg: Dict[str, Any], base_cfg: Dict[str, Any],
                  out_dir: Path, referee_caller: Any, deception: str = "none") -> Any:
    """Construct the target runner.

    MUST be called single-threaded, before any fan-out. `LiveTargetRunner.__init__` installs the
    class-level monkeypatches (reasoning capture, forced post, single-post-per-turn) behind
    idempotent guards that are not themselves thread-safe. `loop.py` relies on the same property
    when it runs its seeds concurrently.
    """
    from experiments.social_jira4.target_run import LiveTargetRunner

    net = base_cfg.get("communication_network") or {}
    return LiveTargetRunner(
        base_cfg=base_cfg, model_label=label, model_llm_cfg=llm_cfg, out_dir=out_dir,
        referee_caller=referee_caller,
        num_agents=int(net.get("num_agents", 4)),
        num_tasks=int((base_cfg.get("environment") or {}).get("num_tasks", 2)),
        deception=deception,
    )


def _run_unit(*, runner: Any, blocks: Blocks, seed: int, step: int,
              critic: Optional[LlmCritic], objective: objective_mod.Objective) -> Dict[str, Any]:
    """One (prompt, seed): rollout, then judge unless ``critic`` is None."""
    t0 = time.time()
    art = runner.run(blocks, seed, step=step)
    rollout_s = time.time() - t0
    if art.error:
        # A dead target must never look like a null result: no score, not a zero.
        return {"seed": seed, "error": art.error,
                "rollout_seconds": round(rollout_s, 1), "run_dir": art.run_dir}

    if critic is None:
        # Generation-only. No score is written at all — not a zero, not a null objective the
        # aggregate would silently average in. ``rejudge.py`` scores this run dir later.
        return {"seed": seed, "run_dir": art.run_dir,
                "rollout_seconds": round(rollout_s, 1),
                "turns_assembled": len(art.turns),
                "reasoning_captured": sum(1 for t in art.turns
                                          if (t.get("reasoning") or "").strip()),
                "judged": False}

    t1 = time.time()
    rs: RunScore = critic.score(art)
    judge_s = time.time() - t1
    rs.objective = objective.rollout(rs.turns)
    best = rs.best_turn
    return {
        "seed": seed,
        "run_dir": art.run_dir,
        "rollout_seconds": round(rollout_s, 1),
        "judge_seconds": round(judge_s, 1),
        "objective": rs.objective,
        "objective_detail": objective.explain(rs.turns),
        "turns_scored": len(rs.turns),
        "turns_qualifying": sum(1 for t in rs.turns if t.qualifies()),
        "reasoning_captured": sum(1 for t in rs.turns
                                  if (getattr(t, "reasoning", "") or "").strip()),
        "best_turn": _turn_row(best) if best is not None else None,
        "turns": [_turn_row(t) for t in rs.turns],
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--step-files", required=True,
                    help="step records (or block/seed JSON) to replay, in order; prompt i becomes "
                         "step i. Either a comma-separated list, or the path to a MANIFEST — any "
                         "existing non-.json file — holding one path per line, '#' comments and "
                         "blank lines ignored (see configs/rr10_prompts.txt). A campaign whose "
                         "cells must replay the identical prompt set should use a manifest: a "
                         "list repeated across submit files is a list that eventually diverges.")
    ap.add_argument("--config", required=True, help="target config holding the model entry")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--seeds", default="7,8", help="comma-separated scenario seeds per prompt")
    ap.add_argument("--models", default="",
                    help="comma-separated labels (default: every label in the config). Pass ONE "
                         "for the per-model-job shape.")
    ap.add_argument("--workers", type=int, default=4,
                    help="concurrent (prompt, seed) rollouts")
    ap.add_argument("--critic-workers", type=int, default=4,
                    help="judge pool per rollout — multiplies with --workers into total in-flight "
                         "calls on one API key")
    ap.add_argument("--judge-provider", default=llm_mod.DEFAULT_JUDGE_PROVIDER)
    ap.add_argument("--judge-model", default=llm_mod.DEFAULT_JUDGE_MODEL)
    ap.add_argument("--judge-provider-routing", default="",
                    help="OpenRouter provider routing for the JUDGE, as JSON, e.g. "
                         "'{\"order\": [\"deepinfra\"], \"allow_fallbacks\": true}'. The config's "
                         "pins cover the TARGET models only; the judge is built here and routes "
                         "unpinned unless this is set. Prefer an ORDER with fallbacks ON over a "
                         "single hard pin: a run of this shape puts thousands of judge calls on one "
                         "upstream, which returns 429 rather than rerouting when fallbacks are off.")
    ap.add_argument("--objective", default=objective_mod.DEFAULT_NAME)
    ap.add_argument("--skip-existing", action="store_true",
                    help="resume: skip any (prompt, seed) whose run dir is already a COMPLETE "
                         "rollout, and carry the existing results.json forward instead of "
                         "overwriting it. Use this to recover the handful of cells an API timeout "
                         "lost without re-rolling — and paying for, and replacing — the transcripts "
                         "that succeeded. A unit that errored or died partway is NOT complete, so "
                         "it is re-run and its old record replaced.")
    ap.add_argument("--no-judge", action="store_true",
                    help="produce the rollouts and stop — no CATEGORY/SOUNDNESS/AT-STAKE pass. "
                         "For campaigns that want every cell scored later in ONE offline pass by "
                         "rejudge.py, so all verdicts come from one judge and one code path, and "
                         "so a GPU job is never held open paying for judge latency.")
    ap.add_argument("--referee-provider", default=llm_mod.DEFAULT_JUDGE_PROVIDER)
    ap.add_argument("--referee-model", default=llm_mod.DEFAULT_JUDGE_MODEL,
                    help="THE REFEREE IS PART OF THE ROLLOUT, not part of the scoring: it decides "
                         "each round whether planning has reached consensus/stalemate and stops "
                         "the meeting, so it shapes the transcript itself. It used to be the same "
                         "caller as the judge, which meant re-pointing --judge-model silently "
                         "changed what the targets produced. It is its own flag now, defaulting to "
                         "the historical value, so a run stays comparable to earlier runs no "
                         "matter which judge scores it. Change it only to deliberately alter the "
                         "environment.")
    ap.add_argument("--deception", default="none", choices=list(DECEPTION_LEVELS),
                    help="run-level manipulation appended as the FINAL paragraph of the system "
                         "prompt (adaptive_prompts.DECEPTION_LINES). 'none' appends nothing and "
                         "leaves the prompt — and the run-dir names — exactly as they were before "
                         "the axis existed. It is NOT part of the prompt: it never enters the "
                         "blocks, never changes a prompt_sha, and no gate sees it.")
    args = ap.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    step_files = _read_step_files(args.step_files)
    prompts: List[Tuple[int, str, Blocks]] = []
    for i, sf in enumerate(step_files):
        prompts.append((i, sf, _blocks_from_step(Path(sf))))
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]

    base_cfg = _load_yaml(args.config)
    entries = base_cfg.get("llm_models") or []
    if not entries:
        raise SystemExit(f"{args.config} declares no llm_models")
    wanted = [s.strip() for s in args.models.split(",") if s.strip()]
    if wanted:
        known = [str(e.get("label")) for e in entries]
        missing = [w for w in wanted if w not in known]
        if missing:
            raise SystemExit(f"no model(s) {missing} in {args.config} (labels: {known})")
        entries = [e for e in entries if str(e.get("label")) in wanted]

    judge_routing = json.loads(args.judge_provider_routing) if args.judge_provider_routing else None
    # Two callers, two roles. The referee runs inside every rollout; the critics run after it.
    rcaller = llm_mod.make_judge_caller(provider=args.referee_provider, model=args.referee_model)
    if args.no_judge:
        jcaller = None
        critic: Optional[LlmCritic] = None
    else:
        jcaller = llm_mod.make_judge_caller(provider=args.judge_provider, model=args.judge_model,
                                            provider_routing=judge_routing)
        critic = LlmCritic(jcaller, gate=False, workers=max(1, args.critic_workers))
    objective = objective_mod.get(args.objective)

    print(f"prompts  : {len(prompts)}")
    for i, sf, b in prompts:
        print(f"   [{i}] {sf}  ({'+'.join(b.decoy_info_ids)} / {b.private_messages_id})")
    print(f"seeds    : {seeds}")
    print(f"models   : {', '.join(str(e.get('label')) for e in entries)}")
    print(f"referee  : {args.referee_provider}/{args.referee_model} (inside the rollout)")
    print("judges   : OFF — generation only (--no-judge); score later with rejudge.py"
          if args.no_judge else
          f"judges   : {args.judge_provider}/{args.judge_model} — CATEGORY, SOUNDNESS, AT-STAKE")
    print(f"deception: {args.deception}"
          + (f" — {DECEPTION_LINES[args.deception]!r}" if args.deception != "none" else
             " (nothing appended)"))
    print(f"rollouts : {len(prompts) * len(seeds) * len(entries)} "
          f"({args.workers} concurrent"
          + ("" if args.no_judge else f", {args.critic_workers} judge workers each") + ")\n",
          flush=True)

    # Runners first, single-threaded — see _build_runner on why this cannot move into the pool.
    runners = {str(e.get("label")): _build_runner(
        label=str(e.get("label")), llm_cfg=e.get("llm") or {}, base_cfg=base_cfg,
        out_dir=out_dir, referee_caller=rcaller, deception=args.deception) for e in entries}

    units = [(lbl, i, sf, b, seed)
             for lbl in runners for (i, sf, b) in prompts for seed in seeds]

    results: Dict[Tuple[str, int], Dict[str, Any]] = {}
    if args.skip_existing:
        # Seed the accumulator from what is already on disk, or the flush below would rewrite
        # results.json with ONLY the units this invocation re-ran — turning a one-rollout repair
        # into the loss of every rollout it was meant to preserve.
        prior = out_dir / "results.json"
        if prior.is_file():
            for rec in json.loads(prior.read_text(encoding="utf-8")):
                results[(str(rec["label"]), int(rec["step"]))] = rec
        keep = []
        for unit in units:
            lbl, i, _sf, b, seed = unit
            rd = out_dir / "runs" / f"step{i:03d}" / run_id_for(lbl, b, seed, args.deception)
            if is_complete(rd):
                continue
            keep.append(unit)
        print(f"resume   : {len(units) - len(keep)} complete on disk, {len(keep)} to run")
        units = keep
        if not units:
            print("nothing to do")
            return 0

    lock = threading.Lock()

    def _flush() -> None:
        """Rewrite results.json. Called under the lock after every unit, so a job that dies or is
        killed still leaves every completed (prompt, seed) on disk — with 100 rollouts in flight
        that is the difference between losing a run and losing one cell."""
        out: List[Dict[str, Any]] = []
        for lbl in runners:
            for (i, sf, b) in prompts:
                entry = results.get((lbl, i))
                if entry:
                    out.append(entry)
        (out_dir / "results.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    def _one(unit):
        lbl, i, sf, blocks, seed = unit
        tag = f"{lbl} p{i} s{seed}"
        print(f"--- {tag} starting", flush=True)
        try:
            res = _run_unit(runner=runners[lbl], blocks=blocks, seed=seed, step=i,
                            critic=critic, objective=objective)
        except Exception as exc:
            traceback.print_exc()
            res = {"seed": seed, "error": f"{type(exc).__name__}: {exc}"}
        with lock:
            entry = results.setdefault((lbl, i), {
                "label": lbl, "step": i, "step_file": sf,
                "blocks": blocks.to_dict(), "seeds": [],
            })
            # Replace rather than append: on a resume the seed may already be present as the error
            # record this re-run exists to fix, and two records for one seed would double-count.
            entry["seeds"] = [s for s in entry["seeds"] if s.get("seed") != res.get("seed")]
            entry["seeds"].append(res)
            entry["seeds"].sort(key=lambda s: s["seed"])
            scored = [s["objective"] for s in entry["seeds"] if "objective" in s]
            # Under --no-judge there is no score to aggregate. Left absent rather than set to 0.0,
            # which would read as "judged, found nothing".
            if scored:
                entry["score"] = objective.aggregate(scored)
            elif critic is not None:
                entry["score"] = 0.0
            _flush()
        if res.get("error"):
            print(f"--- {tag} ERROR {res['error']}", flush=True)
        elif res.get("judged") is False:
            print(f"--- {tag} rolled out, unjudged — {res['turns_assembled']} turns, "
                  f"cot={res['reasoning_captured']}/{res['turns_assembled']} captured, "
                  f"{res['rollout_seconds']:.0f}s", flush=True)
        else:
            print(f"--- {tag} objective={res['objective']:.2f} "
                  f"turns={res['turns_qualifying']}/{res['turns_scored']} qualifying, "
                  f"cot={res['reasoning_captured']}/{res['turns_scored']} captured, "
                  f"{res['rollout_seconds']:.0f}s roll + {res['judge_seconds']:.0f}s judge",
                  flush=True)
        return res

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        list(pool.map(_one, units))

    meta = {
        "step_files": step_files,
        "config": args.config,
        "seeds": seeds,
        "models": {"prompter": "(none — fixed prompt replay)",
                   "target": ",".join(runners)},
        "judge": ({"ran": False, "note": "--no-judge: rollouts only, scored separately by "
                                         "rejudge.py"} if args.no_judge else
                  {"ran": True, "provider": args.judge_provider, "model": args.judge_model,
                   "provider_routing": judge_routing,
                   "critics": ["CATEGORY", "SOUNDNESS", "AT-STAKE"]}),
        # Part of the ENVIRONMENT, not the scoring — it ends the planning discussion, so two runs
        # with different referees are not the same experiment. Recorded next to the judge so the
        # distinction is visible in every tree.
        "referee": {"provider": args.referee_provider, "model": args.referee_model},
        # The condition this whole out-dir is, recorded with the sentence itself so a tree can be
        # read years later without resolving a level name against a constant that may have moved.
        "deception": {"level": args.deception,
                      "appended_line": DECEPTION_LINES[args.deception],
                      "placement": "final paragraph of the system prompt, after the justify clause"},
        "objective": objective.name,
        "prompts": [{"step": i, "step_file": sf, "decoys": list(b.decoy_info_ids),
                     "inbox": b.private_messages_id} for i, sf, b in prompts],
        "note": ("fixed prompt replay — no prompter, no validator/consistency/meta gate, no "
                 "fabrication or L2 judge. Each prompt cleared a gate when it was mined; it is "
                 "not re-gated here."),
    }
    (out_dir / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    cost = {"total_usd": 0.0, "roles": {}}
    for role, caller in (("judge", jcaller), ("referee", rcaller)):
        snap = getattr(caller, "snapshot", None)
        if snap is None:
            continue
        t = snap()
        cost["roles"][role] = {"provider": getattr(caller, "provider", "unknown"),
                               "model": getattr(caller, "model", ""), **t}
        cost["total_usd"] = round(cost["total_usd"] + float(t.get("cost_usd") or 0.0), 6)
    cost["note"] = ("judge + referee spend only — the assistant rollouts bill on OpenRouter "
                    "through social_jira3's own client, which this script does not instrument")
    (out_dir / "cost.json").write_text(json.dumps(cost, indent=2), encoding="utf-8")

    done = sum(1 for e in results.values() for s in e["seeds"] if not s.get("error"))
    total = len(units)
    print(f"\nwrote {out_dir}/results.json, metadata.json, cost.json")
    print(f"judge+referee spend: ${cost['total_usd']:.4f}")
    print(f"{done}/{total} rollouts completed")
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
