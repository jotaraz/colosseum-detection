"""Make a `fixed_prompt_run.py` output dir browsable in the sj4 viewer.

The viewer's unit of display is an OPTIMIZER RUN: `metadata.json` + `history.jsonl` +
`steps/step_NNN.json`, one step per prompter attempt, each step holding one seed per rollout.
`fixed_prompt_run.py` writes none of that — it has no prompter and no trajectory, just a
`results.json`. This script synthesises the missing shape so those runs appear alongside the real
ones, without touching the rollout dirs, which are already jira3-shaped and load unchanged.

MAPPING: ONE STEP PER PROMPT, one seed entry per seed — which is exactly what the viewer already
means by those words, so nothing has to be reinterpreted. The only fiction left is the trajectory
line: prompt i+1 does not build on prompt i, so "best so far" is a running max over unrelated
prompts. Read the chips as a bar chart, not a search.

Every step is marked `prompter_source: "warm_start"`. Not cosmetic — the viewer's `_step_state`
renders warm-start steps distinctly, and the codebase already uses that label to mean "a fixed
prompt was replayed, no model wrote it, and there is no prompter reasoning behind it". Which is
what these runs are.

Synthetic fields are marked as such in the files themselves, so nobody later mistakes a
reconstruction for a recording.

    python -m experiments.social_jira4.fixed_prompt_to_viewer outputs/rr_qwen outputs/rr_kimi ...

Idempotent: re-run after more rollouts land and the whole shape is rebuilt from the current
`results.json`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

SYNTHETIC = ("synthesised by fixed_prompt_to_viewer.py so the viewer can browse this run — "
             "this was a fixed-prompt replay, NOT an optimizer trajectory")


def _rollout_config(run_dir: Path, label: str) -> Dict[str, Any]:
    """Any rollout's `run_config.json`, which records the blocks and seed it actually ran with.

    Resolved by label under this run's own `runs/` rather than through the stored `run_dir`, so it
    still works after the outputs tree is rsynced to a different machine (the stored path is
    relative to the CLUSTER repo root).
    """
    for cand in (run_dir / "runs").glob(f"step*/{label}__*/run_config.json"):
        try:
            return json.loads(cand.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


def _prompter_system(meta: Dict[str, Any]) -> str:
    prompts = meta.get("prompts") or []
    lines = "\n".join(
        f"- **step {p.get('step')}** — `{p.get('step_file')}`  "
        f"({'+'.join(p.get('decoys') or [])} / {p.get('inbox')})" for p in prompts)
    return (
        "# No prompter\n\n"
        "This run replayed already-written prompts; no prompter model was called, so there is no "
        "scaffold to show here.\n\n"
        f"Each *step* is one **prompt**, replayed at seeds {meta.get('seeds')}:\n\n{lines}\n\n"
        f"- **judges:** {json.dumps(meta.get('judge', {}))}\n\n"
        "The trajectory line is a running max over unrelated prompts and carries no meaning — read "
        "the chips as a bar chart. Gate panels are empty because no gate ran: each prompt had "
        "already cleared one when it was mined.\n"
    )


def convert(run_dir: Path) -> str:
    results_p = run_dir / "results.json"
    if not results_p.exists():
        return f"{run_dir.name}: no results.json — nothing to convert (still running?)"
    results: List[Dict[str, Any]] = json.loads(results_p.read_text(encoding="utf-8"))
    meta_p = run_dir / "metadata.json"
    meta: Dict[str, Any] = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}

    objective_name = meta.get("objective") or next(
        (str((s.get("objective_detail") or {}).get("objective_name") or "")
         for r in results for s in (r.get("seeds") or [])
         if (s.get("objective_detail") or {}).get("objective_name")), "weighted_count")
    label = str(results[0].get("label", "")) if results else ""

    steps_dir = run_dir / "steps"
    steps_dir.mkdir(exist_ok=True)
    for old in steps_dir.glob("step_*.json"):
        old.unlink()

    history: List[Dict[str, Any]] = []
    for r in sorted(results, key=lambda x: int(x.get("step", 0))):
        i = int(r.get("step", 0))
        seeds_in = r.get("seeds") or []
        errored = all(s.get("error") for s in seeds_in) if seeds_in else True
        score = float(r.get("score", 0.0) or 0.0)
        # RECOVER BLOCKS FROM THE ROLLOUT if results.json predates carrying them: a job killed
        # mid-flight has no metadata.json, and an empty prompt panel is exactly what you do not
        # want on the runs most likely to need inspecting.
        blocks = r.get("blocks") or _rollout_config(run_dir, label).get("blocks") or {}
        rationale = f"prompt {i}: {Path(str(r.get('step_file', ''))).name}"
        if errored:
            first_err = next((s.get("error") for s in seeds_in if s.get("error")), "all seeds failed")
            rationale += f" — ERROR: {first_err}"

        seed_entries = []
        obj_seeds = []
        for s in seeds_in:
            seed_entries.append({
                "seed": s.get("seed"),
                "objective": s.get("objective", 0.0),
                "run_dir": s.get("run_dir"),
                "error": s.get("error"),
                "turns": s.get("turns") or [],
            })
            od = dict(s.get("objective_detail") or {})
            od["seed"] = s.get("seed")
            obj_seeds.append(od)

        step_obj = {
            "schema": 4, "step": i, "opt_step": i, "repair": 0,
            "cb_ok": not errored,
            "gate": "" if not errored else "rollout",
            "cb_reason": f"no gate ran — {SYNTHETIC}",
            "score": score,
            "duration_s": sum(float(s.get("rollout_seconds", 0) or 0)
                              + float(s.get("judge_seconds", 0) or 0) for s in seeds_in),
            "usage": {},
            "prompter": {"source": "warm_start", "rationale": rationale, "reasoning": "",
                         "raw": "", "note": SYNTHETIC, "model_label": label,
                         "step_file": r.get("step_file")},
            "cb": {"ok": True, "reason": f"not run — {SYNTHETIC}"},
            "cons": {"ran": False},
            "meta": None,
            "blocks": blocks,
            "objective": {"name": objective_name,
                          "description": f"{objective_name} (fixed prompt, {len(seeds_in)} seeds)",
                          "per_seed": [s.get("objective", 0.0) for s in seeds_in],
                          "aggregate": score, "seeds": obj_seeds},
            "seeds": seed_entries,
        }
        (steps_dir / f"step_{i:03d}.json").write_text(json.dumps(step_obj, indent=2),
                                                      encoding="utf-8")

        best_span = ""
        for s in seeds_in:
            bt = s.get("best_turn") or {}
            if bt.get("spans"):
                best_span = bt["spans"][0]
                break
        history.append({
            "step": i, "cb_ok": not errored, "gate": "" if not errored else "rollout",
            "cb_reason": step_obj["cb_reason"], "score": score, "blocks": blocks,
            "seed_scores": [s.get("objective", 0.0) for s in seeds_in],
            "best_lie": best_span,
            "prompter_rationale": rationale, "prompter_reasoning_chars": 0,
            "prompter_source": "warm_start",
            "duration_s": step_obj["duration_s"], "usage": {},
        })

    (run_dir / "history.jsonl").write_text(
        "".join(json.dumps(h) + "\n" for h in history), encoding="utf-8")

    ran = [h for h in history if h["cb_ok"]]
    if ran:
        top = max(ran, key=lambda h: h["score"])
        (run_dir / "best.json").write_text(json.dumps({
            "step": top["step"], "score": top["score"], "blocks": top["blocks"],
            "model_label": label, "note": SYNTHETIC}, indent=2), encoding="utf-8")

    meta.update({
        "started_at": meta.get("started_at", ""),
        "mode": "live", "step_schema": 4, "steps": len(results),
        "objective": objective_name,
        "objective_description": f"{objective_name} — fixed prompts, {label}",
        "models": {"prompter": "(none — fixed prompt replay)", "target": label,
                   "target_per_seed": f"every seed = {label}"},
        "viewer_shape": SYNTHETIC,
        "viewer_step_meaning": "one step == one PROMPT; the trajectory is not a search",
    })
    meta.setdefault("offline", False)
    meta_p.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (run_dir / "prompter_system.md").write_text(_prompter_system(meta), encoding="utf-8")

    n_seeds = sum(len(r.get("seeds") or []) for r in results)
    return f"{run_dir.name}: {len(results)} step(s), {n_seeds} rollout(s) — {label}"


def main(argv: List[str]) -> int:
    if not argv:
        print("usage: fixed_prompt_to_viewer.py <run-dir> [<run-dir> ...]")
        return 2
    for a in argv:
        print(convert(Path(a)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
