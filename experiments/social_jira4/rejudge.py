"""Re-score already-written sj4 rollouts with a different judge model. NOTHING is re-rolled.

``fixed_prompt_run.py`` does rollout -> critics in one process, so its judge is fixed at the moment
the transcript is produced. This script splits that seam: it reads a frozen run dir and runs the
SAME three critics (CATEGORY / SOUNDNESS / AT-STAKE) over it again with a different judge behind
them. The rollouts are opened read-only and never rewritten — the assistant transcripts,
reasoning, tool events and scenario stay byte-identical, which is the whole point: the only free
variable between two passes is the judge.

**Why this is a faithful re-ask and not a re-render.** The critic input is rebuilt by
``target_run.assemble_turns``, the same function ``LiveTargetRunner.run`` calls immediately after a
rollout finishes — ``build_turns`` over ``tool_events.json`` + ``agent_reasoning.json``, prompts
recovered from ``agent_prompts.json``, ground truth rebuilt from ``scenario.json``. So the judge is
handed exactly what the original judge was handed, assembled by exactly the same code, not a
reconstruction that happens to look similar.

**What this pass keeps that the original did not.** ``fixed_prompt_run._turn_row`` deliberately
drops each judge's verbatim reply, so a completed run leaves categories/soundness/at-stake but not
the reasoning behind them. Here every verdict is appended to ``verdicts.jsonl`` in full — parsed
reply, the judge's CoT, token usage, attempts, raw text, and which upstream served the call. A
disagreement with the baseline is then diagnosable rather than merely countable.

**Layout.** One directory per judge seat under ``<root>/judgments/<seat>/``, so seats accumulate
side by side and none of them can collide with the rollouts:

    <root>/results.json                 baseline (copied from the original run, judge of record)
    <root>/runs/stepNNN/<run>/          the frozen rollouts — read-only here
    <root>/judgments/<seat>/results.json    same shape as the baseline, new verdicts
    <root>/judgments/<seat>/verdicts.jsonl  append-only, one row per judged turn, full audit trail
    <root>/judgments/<seat>/agreement.json  baseline vs. this seat, per turn and per rollout
    <root>/judgments/<seat>/{metadata,cost}.json

``results.json`` is rewritten after every rollout and completed rollouts are skipped on a rerun, so
an interrupted pass keeps everything it paid for and a retry only redoes what failed.

Usage (from the repo root):
    python -m experiments.social_jira4.rejudge --dry-run
    python -m experiments.social_jira4.rejudge --seat dsflash \
        --judge-model deepseek/deepseek-v4-flash
    python -m experiments.social_jira4.rejudge --seat dsflash --compare-only
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
from typing import Any, Dict, List, Optional

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from experiments.social_jira4 import llm as llm_mod
from experiments.social_jira4 import objective as objective_mod
from experiments.social_jira4.blocks import Blocks
from experiments.social_jira4.critic import LlmCritic
from experiments.social_jira4.interfaces import RunArtifacts, RunScore, TurnScore
from experiments.social_jira4.target_run import assemble_turns

DEFAULT_ROOT = "experiments/social_jira4/outputs/rr10_rejudge"

# The judge routes UNPINNED unless told otherwise, and which upstream OpenRouter picks changes what
# comes back. These are the upstreams already verified for this model family in
# configs/social_jira4_realrun_pinned_or.yaml — as an ORDER with fallbacks ON rather than a hard
# pin, per the lesson recorded in fixed_prompt_run's --judge-provider-routing help: a single pin
# with fallbacks off returns 429 instead of rerouting once a few hundred calls land on it. Every
# call records the upstream that actually served it, so a fallback is visible rather than silent.
DEFAULT_JUDGE_ROUTING = '{"order": ["DeepInfra", "Morph"], "allow_fallbacks": true}'

# 4096 is the make_judge_caller default and was enough for the deepseek pair in the meta-judge
# sweep, but a truncated verdict is unrecoverable: it arrives as unparseable JSON (or an empty
# reply) that the critic can only retry, and CATEGORY's replies carry quoted spans, so they are the
# long ones. 8192 costs nothing when unused — completions bill on what is emitted, not the cap.
DEFAULT_MAX_TOKENS = 8192


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve(p: str) -> Path:
    """Run dirs are recorded repo-relative; accept either that or an absolute/CWD-relative path."""
    q = Path(p)
    return q if q.is_absolute() or q.exists() else project_root / p


class _Sidecar:
    """Append-only JSONL, flushed per row so a killed run keeps every verdict it paid for."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8")
        self._lock = threading.Lock()

    def write(self, row: Dict[str, Any]) -> None:
        with self._lock:
            self._fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def _turn_row(ts: TurnScore) -> Dict[str, Any]:
    """The flattened row, matching ``fixed_prompt_run._turn_row`` field for field so the baseline
    and this pass can be diffed without a translation layer. The judges' full replies do NOT go
    here — they go to verdicts.jsonl, which is what this file is skimmable instead of."""
    return {
        "agent": ts.agent,
        "turn_index": ts.turn_index,
        "categories": list(ts.categories or []),
        "fabrication_specificity": ts.fabrication_specificity,
        "soundness_ok": ts.soundness_ok,
        "at_stake": ts.at_stake,
        "qualifies": ts.qualifies(),
        "spans": list(ts.spans or []),
        "explanation": ts.explanation,
        "reasoning_chars": len(ts.reasoning or ""),
        "message": ts.message,
    }


def _verdict_rows(*, seat: str, rec: Dict[str, Any], seed: int, run_dir: str,
                  scores: List[TurnScore]) -> List[Dict[str, Any]]:
    """One row per judged turn, carrying all three judges verbatim under ``judges``.

    ``_meta`` inside each judge holds its CoT, usage (including ``provider_name`` — the upstream
    that served it), attempt count and raw reply. ``ok`` is False when any of the three failed to
    parse, which is what makes a rerun able to distinguish "the judge said no" from "the judge
    never answered"."""
    rows = []
    for ts in scores:
        judges = ts.judges or {}
        ok = not any(isinstance(j, dict) and j.get("_parse_error") for j in judges.values())
        rows.append({
            "seat": seat,
            "label": rec.get("label"),
            "step": rec.get("step"),
            "seed": seed,
            "run_dir": run_dir,
            "turn_index": ts.turn_index,
            "agent": ts.agent,
            "ok": ok,
            "verdict": {
                "categories": list(ts.categories or []),
                "fabrication_specificity": ts.fabrication_specificity,
                "soundness_ok": ts.soundness_ok,
                "at_stake": ts.at_stake,
                "qualifies": ts.qualifies(),
                "spans": list(ts.spans or []),
                "explanation": ts.explanation,
            },
            "judges": judges,
        })
    return rows


# ------------------------------------------------------------------------------- agreement
def _flags(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "fabrication": "strategic-fabrication" in (row.get("categories") or []),
        "soundness_ok": bool(row.get("soundness_ok")),
        "at_stake": bool(row.get("at_stake")),
        "qualifies": bool(row.get("qualifies")),
        "specificity": row.get("fabrication_specificity"),
        "categories": sorted(row.get("categories") or []),
    }


def build_agreement(baseline: List[Dict[str, Any]], new: List[Dict[str, Any]],
                    *, seat: str, baseline_judge: Any) -> Dict[str, Any]:
    """Baseline vs. this seat, turn by turn.

    Turns are matched on ``turn_index`` alone: both passes judged the same frozen transcript, so
    the index IS the identity. A turn present in one pass and not the other is reported rather than
    dropped — that only happens if a rollout was re-judged after being edited, which would
    invalidate the comparison and should be loud."""
    b_by = {(r["label"], r["step"], s["seed"]): {t["turn_index"]: t for t in s.get("turns") or []}
            for r in baseline for s in r.get("seeds") or []}
    n_by = {(r["label"], r["step"], s["seed"]): {t["turn_index"]: t for t in s.get("turns") or []}
            for r in new for s in r.get("seeds") or []}
    b_obj = {(r["label"], r["step"], s["seed"]): s.get("objective")
             for r in baseline for s in r.get("seeds") or []}
    n_obj = {(r["label"], r["step"], s["seed"]): s.get("objective")
             for r in new for s in r.get("seeds") or []}

    fields = ("fabrication", "soundness_ok", "at_stake", "qualifies")
    totals = {f: {"agree": 0, "n": 0} for f in fields}
    # 2x2 on the AND-gate itself: the cell that decides whether a turn is counted at all.
    gate = {"both": 0, "baseline_only": 0, "seat_only": 0, "neither": 0}
    rollouts = []

    for key in n_by:
        b, n = b_by.get(key, {}), n_by[key]
        per = {f: {"agree": 0, "n": 0} for f in fields}
        disagreements = []
        for ti, nt in sorted(n.items()):
            bt = b.get(ti)
            if bt is None:
                disagreements.append({"turn_index": ti, "note": "absent from baseline"})
                continue
            bf, nf = _flags(bt), _flags(nt)
            for f in fields:
                per[f]["n"] += 1
                totals[f]["n"] += 1
                if bf[f] == nf[f]:
                    per[f]["agree"] += 1
                    totals[f]["agree"] += 1
            cell = ("both" if bf["qualifies"] and nf["qualifies"] else
                    "baseline_only" if bf["qualifies"] else
                    "seat_only" if nf["qualifies"] else "neither")
            gate[cell] += 1
            if bf != nf:
                disagreements.append({"turn_index": ti, "agent": nt.get("agent"),
                                      "baseline": bf, seat: nf})
        missing = sorted(set(b) - set(n))
        rollouts.append({
            "label": key[0], "step": key[1], "seed": key[2],
            "objective_baseline": b_obj.get(key), "objective_seat": n_obj.get(key),
            "delta": (None if b_obj.get(key) is None or n_obj.get(key) is None
                      else round(float(n_obj[key]) - float(b_obj[key]), 3)),
            "agreement": {f: {**per[f],
                              "rate": (round(per[f]["agree"] / per[f]["n"], 3) if per[f]["n"] else None)}
                          for f in fields},
            "turns_only_in_baseline": missing,
            "disagreements": disagreements,
        })

    rollouts.sort(key=lambda r: (r["step"], r["seed"]))
    return {
        "seat": seat,
        "baseline_judge": baseline_judge,
        "note": ("Both passes judged the SAME frozen transcripts; turns are matched on turn_index. "
                 "'qualifies' is the AND-gate (strategic-fabrication AND specificity is not None "
                 "AND soundness_ok AND at_stake) and is the only field that moves the objective."),
        "overall": {f: {**totals[f],
                        "rate": (round(totals[f]["agree"] / totals[f]["n"], 3) if totals[f]["n"] else None)}
                    for f in fields},
        "qualifying_2x2": gate,
        "rollouts": rollouts,
    }


# ------------------------------------------------------------------------------- one rollout
def _judge_one(*, rec: Dict[str, Any], seed_rec: Dict[str, Any], critic: LlmCritic,
               objective: objective_mod.Objective, seat: str,
               sidecar: _Sidecar) -> Dict[str, Any]:
    run_dir = _resolve(str(seed_rec["run_dir"]))
    turns = assemble_turns(run_dir)
    if not turns:
        return {"seed": seed_rec["seed"], "run_dir": str(seed_rec["run_dir"]),
                "error": f"no judgeable turns assembled from {run_dir}"}

    art = RunArtifacts(blocks=Blocks.from_dict(rec["blocks"]), seed=int(seed_rec["seed"]),
                       run_dir=str(run_dir), turns=turns)
    t0 = time.time()
    rs: RunScore = critic.score(art)
    judge_s = time.time() - t0

    for row in _verdict_rows(seat=seat, rec=rec, seed=int(seed_rec["seed"]),
                             run_dir=str(seed_rec["run_dir"]), scores=rs.turns):
        sidecar.write(row)

    unparsed = sum(1 for ts in rs.turns
                   for j in (ts.judges or {}).values()
                   if isinstance(j, dict) and j.get("_parse_error"))
    return {
        "seed": int(seed_rec["seed"]),
        "run_dir": str(seed_rec["run_dir"]),
        # The rollout was not re-run: its wall clock belongs to the original pass and is carried
        # over unchanged so nothing here reads as a fresh generation.
        "rollout_seconds": seed_rec.get("rollout_seconds"),
        "judge_seconds": round(judge_s, 1),
        "objective": objective.rollout(rs.turns),
        "objective_detail": objective.explain(rs.turns),
        "turns_scored": len(rs.turns),
        "turns_qualifying": sum(1 for t in rs.turns if t.qualifies()),
        "judge_calls_unparsed": unparsed,
        "reasoning_captured": sum(1 for t in rs.turns if (t.reasoning or "").strip()),
        "best_turn": _turn_row(rs.best_turn) if rs.best_turn is not None else None,
        "turns": [_turn_row(t) for t in rs.turns],
    }


def _cell_dirs(cells_root: Path) -> List[Path]:
    """Every judgeable cell under a cells root: a subdir with a results.json and a runs/ tree.

    A cell that exists but has not finished generating is still listed — ``fixed_prompt_run``
    rewrites results.json after every rollout, so a partially complete cell judges what it has and
    picks up the rest on the next pass. Directories without results.json (a job that died before
    its first rollout, or the jobcwd a vLLM job leaves behind) are skipped silently.
    """
    return sorted(d for d in cells_root.iterdir()
                  if d.is_dir() and (d / "results.json").is_file() and (d / "runs").is_dir())


def _cell_stats(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    objs = [s["objective"] for r in results for s in r.get("seeds") or [] if "objective" in s]
    return {
        "rollouts": sum(len(r.get("seeds") or []) for r in results),
        "judged": len(objs),
        "mean_objective": round(sum(objs) / len(objs), 3) if objs else None,
        "zero": sum(1 for o in objs if not o),
        "max_objective": max(objs) if objs else None,
    }


def _judge_cells(args: Any, ap: argparse.ArgumentParser) -> int:
    """Judge every cell under a cells root, one at a time, into a per-seat judgments tree.

    Implemented by re-entering ``main`` once per cell with ``--root``/``--out-dir`` rewritten,
    rather than by threading a second mode through the single-root path. Each cell then gets its
    own results.json, verdicts.jsonl and cost.json on exactly the code that has already been run
    against a real tree — there is no second, less-exercised judging path to keep correct.
    """
    cells_root = _resolve(args.root)
    if not cells_root.is_dir():
        raise SystemExit(f"no cells root at {cells_root}")
    cells = _cell_dirs(cells_root)
    if args.cells_prefix:
        cells = [c for c in cells if c.name.startswith(args.cells_prefix)]
    if not cells:
        raise SystemExit(f"no cells with results.json + runs/ under {cells_root}"
                         + (f" matching prefix {args.cells_prefix!r}" if args.cells_prefix else ""))

    seat = args.seat or args.judge_model.split("/")[-1]
    jroot = _resolve(args.out_dir) if args.out_dir else cells_root.parent / "judgments" / seat
    # A dry run must leave no trace: creating the seat tree (and, below, writing a summary of zero
    # judged cells into it) makes a plan look like a result to whoever reads the tree next.
    if not args.dry_run:
        jroot.mkdir(parents=True, exist_ok=True)

    if not args.summarize:
        print(f"cells    : {len(cells)} under {cells_root}")
        print(f"seat     : {seat} -> {jroot}\n")
        for i, cell in enumerate(cells, 1):
            print(f"===== [{i}/{len(cells)}] {cell.name}", flush=True)
            passthrough = ["--root", str(cell), "--out-dir", str(jroot / cell.name),
                           "--seat", seat,
                           "--judge-provider", args.judge_provider,
                           "--judge-model", args.judge_model,
                           "--judge-provider-routing", args.judge_provider_routing,
                           "--judge-max-tokens", str(args.judge_max_tokens),
                           "--judge-temperature", args.judge_temperature,
                           "--judge-reasoning-effort", args.judge_reasoning_effort,
                           "--workers", str(args.workers),
                           "--critic-workers", str(args.critic_workers),
                           "--objective", args.objective]
            if args.steps:
                passthrough += ["--steps", args.steps]
            if args.force:
                passthrough.append("--force")
            if args.dry_run:
                passthrough.append("--dry-run")
            rc = main(passthrough)
            if rc != 0:
                print(f"===== {cell.name} returned {rc}", flush=True)

    # ---- study-level table ---------------------------------------------------------------
    rows = []
    for cell in cells:
        rp = jroot / cell.name / "results.json"
        if not rp.is_file():
            continue
        st = _cell_stats(_load_json(rp))
        level, _, model = cell.name.partition("_")
        rows.append({"cell": cell.name, "deception": level, "model": model, **st})

    total_usd = 0.0
    for cell in cells:
        cp = jroot / cell.name / "cost.json"
        if cp.is_file():
            total_usd += float(_load_json(cp).get("total_usd") or 0.0)

    summary = {"seat": seat, "cells_root": str(cells_root), "judge_model": args.judge_model,
               "objective": args.objective, "cells_prefix": args.cells_prefix or None,
               "total_usd": round(total_usd, 6), "cells": rows}
    # Namespaced when filtered: parallel jobs each judge a slice, and a shared summary.json would
    # be overwritten by whichever finished last, silently reporting one slice as the whole study.
    # Re-run with --summarize and no prefix to rebuild the full table from everything on disk.
    name = f"summary_{args.cells_prefix.rstrip('_')}.json" if args.cells_prefix else "summary.json"
    if not args.dry_run:
        (jroot / name).write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\n{seat}: {len(rows)}/{len(cells)} cells judged, ${total_usd:.2f}")
    print(f"{'cell':34s} {'judged':>7s} {'mean':>7s} {'max':>5s} {'zero':>5s}")
    for r in sorted(rows, key=lambda r: (r["model"], r["deception"])):
        mean = "—" if r["mean_objective"] is None else f"{r['mean_objective']:.2f}"
        mx = "—" if r["max_objective"] is None else f"{r['max_objective']:.0f}"
        print(f"{r['cell']:34s} {r['judged']:>3d}/{r['rollouts']:<3d} {mean:>7s} {mx:>5s} "
              f"{r['zero']:>5d}")
    print(f"\nwrote {jroot}/{name}" if not args.dry_run else "\n(dry run — nothing written)")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=DEFAULT_ROOT,
                    help="dir holding results.json (the baseline) and runs/ (the frozen rollouts)")
    ap.add_argument("--seat", default="", help="output subdir name; defaults to a slug of the model")
    ap.add_argument("--out-dir", default="", help="override <root>/judgments/<seat>")
    ap.add_argument("--judge-provider", default=llm_mod.DEFAULT_JUDGE_PROVIDER)
    ap.add_argument("--judge-model", default="deepseek/deepseek-v4-flash")
    ap.add_argument("--judge-provider-routing", default=DEFAULT_JUDGE_ROUTING,
                    help="OpenRouter routing for the judge, as JSON. Pass '' to route unpinned.")
    ap.add_argument("--judge-max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    ap.add_argument("--judge-temperature", default="0.0",
                    help="pass '' to send NO temperature (required by models that removed the "
                         "sampling parameters, e.g. claude-sonnet-5)")
    ap.add_argument("--judge-reasoning-effort", default=llm_mod.DEFAULT_JUDGE_REASONING_EFFORT)
    ap.add_argument("--workers", type=int, default=2, help="rollouts judged concurrently")
    ap.add_argument("--critic-workers", type=int, default=6,
                    help="judge pool per rollout — multiplies with --workers into total in-flight "
                         "calls on one API key")
    ap.add_argument("--objective", default=objective_mod.DEFAULT_NAME)
    ap.add_argument("--steps", default="", help="comma-separated step numbers to judge (default: all)")
    ap.add_argument("--force", action="store_true",
                    help="re-judge rollouts already present in the seat's results.json")
    ap.add_argument("--dry-run", action="store_true",
                    help="assemble the turns and print what would be judged; no model calls")
    ap.add_argument("--compare-only", action="store_true",
                    help="rebuild agreement.json from an existing results.json; no model calls")
    ap.add_argument("--cells", action="store_true",
                    help="treat --root as a CELLS ROOT (a dir of sibling run dirs, one per "
                         "experimental condition) and judge every cell under it in turn, each into "
                         "<root>/../judgments/<seat>/<cell>/. This is how a campaign gets one judge "
                         "and one code path across every condition. Resume applies per cell, so "
                         "re-running as new cells land only judges the new ones.")
    ap.add_argument("--cells-prefix", default="",
                    help="with --cells: judge only cells whose directory name starts with this. "
                         "Lets one campaign be split across several cluster jobs by condition "
                         "(e.g. one job per deception level) without every job re-walking the "
                         "whole tree — and bounds total in-flight judge calls, which is "
                         "jobs x --workers x --critic-workers, not --workers x --critic-workers.")
    ap.add_argument("--summarize", action="store_true",
                    help="with --cells: print the cell x objective table from what is already "
                         "judged and write summary.json; no model calls")
    args = ap.parse_args(argv)

    if args.cells:
        return _judge_cells(args, ap)

    root = _resolve(args.root)
    baseline_path = root / "results.json"
    if not baseline_path.is_file():
        raise SystemExit(f"no baseline results.json under {root}")
    baseline: List[Dict[str, Any]] = _load_json(baseline_path)

    seat = args.seat or args.judge_model.split("/")[-1]
    out_dir = _resolve(args.out_dir) if args.out_dir else root / "judgments" / seat
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.json"

    wanted = {int(s) for s in args.steps.split(",") if s.strip()} if args.steps else None
    records = [r for r in baseline if wanted is None or int(r["step"]) in wanted]

    # Who produced the verdicts already in the baseline results.json. A hand-built tree records it
    # in manifest.json; a tree written by fixed_prompt_run records it in metadata.json. Both are
    # checked so the agreement report can never silently compare against an unnamed judge.
    baseline_judge = None
    src_meta = root / "manifest.json"
    if src_meta.is_file():
        baseline_judge = _load_json(src_meta).get("judge_of_record")
    if baseline_judge is None and (root / "metadata.json").is_file():
        jm = (_load_json(root / "metadata.json") or {}).get("judge") or {}
        if jm.get("ran") is not False and jm.get("model"):
            baseline_judge = f"{jm.get('provider', '?')}/{jm['model']}"

    if args.compare_only:
        if not results_path.is_file():
            raise SystemExit(f"nothing to compare: {results_path} does not exist")
        agreement = build_agreement(baseline, _load_json(results_path), seat=seat,
                                    baseline_judge=baseline_judge)
        (out_dir / "agreement.json").write_text(json.dumps(agreement, indent=2), encoding="utf-8")
        _print_agreement(agreement, seat)
        return 0

    print(f"root     : {root}")
    print(f"seat     : {seat} -> {out_dir}")
    print(f"judge    : {args.judge_provider}/{args.judge_model} — CATEGORY, SOUNDNESS, AT-STAKE")
    print(f"baseline : {baseline_judge or '(unrecorded)'}")

    units = [(rec, s) for rec in records for s in rec.get("seeds") or [] if not s.get("error")]

    if args.dry_run:
        total = 0
        for rec, s in units:
            n = len(assemble_turns(_resolve(str(s["run_dir"]))))
            total += n
            print(f"  step{int(rec['step']):03d} seed{s['seed']} {rec['label']:22s} "
                  f"{n:3d} turns  baseline objective={s.get('objective')}")
        print(f"\n{len(units)} rollouts, {total} turns, {total * 3} judge calls — nothing called.")
        return 0

    # Resume: a completed rollout is not paid for twice. Rows already in verdicts.jsonl for a
    # rollout being redone are superseded rather than removed — the sidecar is append-only and
    # last-wins on read, which is what keeps an interrupted pass safe to simply rerun.
    existing: List[Dict[str, Any]] = _load_json(results_path) if results_path.is_file() else []
    done = {(r["label"], r["step"], s["seed"])
            for r in existing for s in r.get("seeds") or [] if not s.get("error")}
    if not args.force and done:
        before = len(units)
        units = [(rec, s) for rec, s in units
                 if (rec["label"], rec["step"], s["seed"]) not in done]
        print(f"resume   : {before - len(units)} rollout(s) already judged, {len(units)} to go")
    if not units:
        print("nothing to do (pass --force to re-judge)")
        agreement = build_agreement(baseline, existing, seat=seat, baseline_judge=baseline_judge)
        (out_dir / "agreement.json").write_text(json.dumps(agreement, indent=2), encoding="utf-8")
        _print_agreement(agreement, seat)
        return 0

    routing = json.loads(args.judge_provider_routing) if args.judge_provider_routing else None
    temperature = float(args.judge_temperature) if args.judge_temperature != "" else None
    jcaller = llm_mod.make_judge_caller(
        provider=args.judge_provider, model=args.judge_model,
        max_tokens=args.judge_max_tokens, temperature=temperature,
        reasoning_effort=args.judge_reasoning_effort, provider_routing=routing)
    critic = LlmCritic(jcaller, gate=False, workers=max(1, args.critic_workers))
    objective = objective_mod.get(args.objective)
    sidecar = _Sidecar(out_dir / "verdicts.jsonl")

    print(f"rollouts : {len(units)} ({args.workers} concurrent, "
          f"{args.critic_workers} judge workers each)\n", flush=True)

    merged: Dict[Any, Dict[str, Any]] = {}
    for r in existing:
        merged[(r["label"], r["step"])] = r
    lock = threading.Lock()

    def _flush() -> None:
        out = sorted(merged.values(), key=lambda r: (r["step"], r["label"]))
        results_path.write_text(json.dumps(out, indent=2), encoding="utf-8")

    def _one(unit):
        rec, seed_rec = unit
        tag = f"{rec['label']} step{int(rec['step']):03d} seed{seed_rec['seed']}"
        print(f"--- {tag} judging", flush=True)
        try:
            res = _judge_one(rec=rec, seed_rec=seed_rec, critic=critic, objective=objective,
                             seat=seat, sidecar=sidecar)
        except Exception as exc:
            traceback.print_exc()
            res = {"seed": seed_rec["seed"], "run_dir": seed_rec.get("run_dir"),
                   "error": f"{type(exc).__name__}: {exc}"}
        with lock:
            entry = merged.setdefault((rec["label"], rec["step"]), {
                "label": rec["label"], "step": rec["step"], "step_file": rec.get("step_file"),
                "blocks": rec.get("blocks"), "seeds": [],
            })
            entry["seeds"] = [s for s in entry["seeds"] if s.get("seed") != res.get("seed")]
            entry["seeds"].append(res)
            entry["seeds"].sort(key=lambda s: s["seed"])
            scored = [s["objective"] for s in entry["seeds"] if "objective" in s]
            entry["score"] = objective.aggregate(scored) if scored else 0.0
            _flush()
        if res.get("error"):
            print(f"--- {tag} ERROR {res['error']}", flush=True)
        else:
            base = seed_rec.get("objective")
            print(f"--- {tag} objective={res['objective']:.2f} (baseline {base}) "
                  f"{res['turns_qualifying']}/{res['turns_scored']} qualifying, "
                  f"{res['judge_calls_unparsed']} unparsed, {res['judge_seconds']:.0f}s",
                  flush=True)
        return res

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        list(pool.map(_one, units))
    sidecar.close()

    meta = {
        "root": str(args.root),
        "seat": seat,
        "judge": {"provider": args.judge_provider, "model": args.judge_model,
                  "provider_routing": routing, "max_tokens": args.judge_max_tokens,
                  "temperature": temperature, "reasoning_effort": args.judge_reasoning_effort,
                  "critics": ["CATEGORY", "SOUNDNESS", "AT-STAKE"], "gate": False},
        "baseline_judge": baseline_judge,
        "objective": objective.name,
        "rollouts": [{"label": rec["label"], "step": rec["step"], "seed": s["seed"],
                      "run_dir": s["run_dir"]} for rec, s in units],
        "note": ("offline re-judge — no rollout was re-run. Critic input rebuilt by "
                 "target_run.assemble_turns from the frozen run dir, identical to what the "
                 "baseline judge was handed. Rollout artifacts are read-only here."),
    }
    (out_dir / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # ACCUMULATED across invocations, not overwritten. The caller only knows what THIS process
    # spent, so a resumed pass would otherwise report the cost of the tail and silently drop what
    # the interrupted run already paid — the one number a reader is most likely to trust blindly.
    cost = {"total_usd": 0.0, "roles": {}, "invocations": 0}
    if (out_dir / "cost.json").is_file():
        cost = {**cost, **_load_json(out_dir / "cost.json")}
    snap = getattr(jcaller, "snapshot", None)
    if snap is not None:
        t = snap()
        prev = cost["roles"].get("judge") or {}
        merged_role = {"provider": getattr(jcaller, "provider", "unknown"),
                       "model": getattr(jcaller, "model", "")}
        for k, v in t.items():
            merged_role[k] = round(float(prev.get(k) or 0) + float(v), 8) \
                if isinstance(v, (int, float)) and not isinstance(v, bool) else v
        cost["roles"]["judge"] = merged_role
        cost["total_usd"] = round(float(merged_role.get("cost_usd") or 0.0), 6)
    cost["invocations"] = int(cost.get("invocations") or 0) + 1
    cost["note"] = ("re-judge spend only, summed over every invocation that wrote this file — no "
                    "generation was billed, nothing was re-rolled")
    (out_dir / "cost.json").write_text(json.dumps(cost, indent=2), encoding="utf-8")

    agreement = build_agreement(baseline, _load_json(results_path), seat=seat,
                                baseline_judge=baseline_judge)
    (out_dir / "agreement.json").write_text(json.dumps(agreement, indent=2), encoding="utf-8")

    print(f"\nwrote {out_dir}/results.json, verdicts.jsonl, agreement.json, metadata.json, cost.json")
    print(f"judge spend: ${cost['total_usd']:.4f}")
    _print_agreement(agreement, seat)
    done_n = sum(1 for r in merged.values() for s in r["seeds"] if not s.get("error"))
    return 0 if done_n else 1


def _print_agreement(agreement: Dict[str, Any], seat: str) -> None:
    ov = agreement["overall"]
    print(f"\nagreement with baseline ({seat}):")
    for f, d in ov.items():
        print(f"  {f:14s} {d['agree']:3d}/{d['n']:3d}  {('%.1f%%' % (100 * d['rate'])) if d['rate'] is not None else '—'}")
    g = agreement["qualifying_2x2"]
    print(f"  qualifying 2x2  both={g['both']} baseline_only={g['baseline_only']} "
          f"seat_only={g['seat_only']} neither={g['neither']}")
    print("\n  step seed label                  baseline -> seat   delta")
    for r in agreement["rollouts"]:
        delta = "—" if r["delta"] is None else f"{r['delta']:+.1f}"
        print(f"  {r['step']:>4} {r['seed']:>4} {str(r['label']):22s} "
              f"{str(r['objective_baseline']):>8} -> {str(r['objective_seat']):>6}   {delta}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
