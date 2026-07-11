"""Headless batch runner for job_appl4 sweeps.

The sweep the batch runner implements: fix a client model, an agent model, an
HR model, and one shared trait list T; provide equal-length lists of skills S,
client system prompts P, and (optional) scripted 5th-check-in messages V; then

    for (s, p, v) in zip(S, P, V):
        create a person with traits T and the SINGLE skill s,
        run the normal autonomous loop with client prompt p,
        and if check-in #5 is reached, send v verbatim as the client's
        comment instead of querying the client LLM.

Each run's full event log and state land in ``outputs/runs/<run_id>/`` as
always; the batch additionally writes ``outputs/batches/<batch_id>/`` with a
``summary.json`` and a readable ``transcript_<label>.txt`` per run.

Usage:
    python run_batch.py config.json [--parallel N] [--dry-run]

Config JSON keys:
    client_model, agent_model, hr_model   OpenRouter model ids ("" = env default)
    cv_model                              dossier writer; independent of
                                          client_model, default gpt-oss-120b
    traits                                 list of strings, shared by all runs
    skills                                 list; each entry a skill NAME (looked
                                           up in world.json to attach its
                                           experiences) or a {name, experiences}
                                           object
    client_prompts                         list of strings, one per run
    fifth_messages                         optional list, one per run; each entry
                                           a string (scripts check-in #5), a
                                           {index: message} object (scripts any
                                           check-ins), or null (client LLM as usual)
    names                                  optional list of names, one per run
    checkin_every, max_tries               ints (defaults 3 / 30)

skills, client_prompts, and fifth_messages/names (if given) must have equal
lengths — they are zipped, one run per triple.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import engine
import world as world_module

BATCHES_DIR = Path(__file__).resolve().parent / "outputs" / "batches"
FIFTH_CHECKIN = 5  # a bare string in fifth_messages scripts this check-in index


def _slug(text: str, max_len: int = 32) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:max_len] or "run"


def _resolve_skill(entry: Any, world: Dict[str, Any]) -> Dict[str, Any]:
    """A config skill entry is either a {name, experiences} object or a bare
    name that is looked up in the world's skill list (to attach experiences)."""
    if isinstance(entry, dict):
        name = str(entry.get("name", "")).strip()
        if not name:
            raise ValueError(f"skill entry has no name: {entry!r}")
        return {"name": name,
                "experiences": [str(e).strip() for e in entry.get("experiences") or []
                                if str(e).strip()]}
    name = str(entry).strip()
    for s in world.get("skills") or []:
        if s.get("name", "").lower() == name.lower():
            return {"name": s["name"], "experiences": list(s.get("experiences") or [])}
    return {"name": name, "experiences": []}


def _resolve_scripted(entry: Any) -> Dict[str, str]:
    """A fifth_messages entry: null/"" -> no scripting; a string -> scripts
    check-in #5; a {index: message} object -> scripts those check-ins."""
    if entry is None:
        return {}
    if isinstance(entry, dict):
        return {str(k): str(v) for k, v in entry.items() if str(v).strip()}
    msg = str(entry).strip()
    return {str(FIFTH_CHECKIN): msg} if msg else {}


def build_specs(config: Dict[str, Any], world: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Expand the config into one run spec per zip(S, P, V) triple."""
    skills = config.get("skills") or []
    prompts_ = config.get("client_prompts") or []
    fifths = config.get("fifth_messages")
    names = config.get("names")
    if len(skills) != len(prompts_):
        raise ValueError(f"skills ({len(skills)}) and client_prompts ({len(prompts_)}) "
                         "must have the same length — they are zipped")
    for key, lst in (("fifth_messages", fifths), ("names", names)):
        if lst is not None and len(lst) != len(skills):
            raise ValueError(f"{key} ({len(lst)}) must match skills ({len(skills)})")
    fifths = fifths if fifths is not None else [None] * len(skills)
    names = names if names is not None else [""] * len(skills)

    traits = [str(t).strip() for t in config.get("traits") or [] if str(t).strip()]
    checkin_every = int(config.get("checkin_every", engine.DEFAULT_CHECKIN_EVERY))
    max_tries = int(config.get("max_tries", engine.DEFAULT_MAX_TRIES))

    specs = []
    for i, (s, p, v, name) in enumerate(zip(skills, prompts_, fifths, names), start=1):
        skill = _resolve_skill(s, world)
        specs.append({
            "label": f"{i:02d}_{_slug(skill['name'])}",
            "traits": traits,
            "skills": [skill],  # one skill per run, by design
            "client_prompt": str(p or ""),
            "scripted_messages": _resolve_scripted(v),
            "name": str(name or ""),
            "checkin_every": checkin_every,
            "max_tries": max_tries,
            "agent_model": str(config.get("agent_model") or ""),
            "hr_model": str(config.get("hr_model") or ""),
            "client_model": str(config.get("client_model") or ""),
            "cv_model": str(config.get("cv_model") or ""),
        })
    return specs


def check_reachability(spec: Dict[str, Any]) -> Optional[str]:
    """Warn when a scripted check-in index can never be reached: check-ins
    fire on rejections 1..max_tries-1 (the run ends at max_tries without a
    check-in), so at most (max_tries-1) // checkin_every happen."""
    if not spec["scripted_messages"]:
        return None
    if spec["checkin_every"] <= 0:
        return "scripted messages set but checkin_every=0 (check-ins disabled)"
    max_checkins = (spec["max_tries"] - 1) // spec["checkin_every"]
    unreachable = [k for k in spec["scripted_messages"] if int(k) > max_checkins]
    if unreachable:
        return (f"scripted check-in(s) {sorted(unreachable, key=int)} can never fire: "
                f"max_tries={spec['max_tries']} / checkin_every={spec['checkin_every']} "
                f"allows at most {max_checkins} check-in(s)")
    return None


def run_one(spec: Dict[str, Any], world: Dict[str, Any],
            progress=print) -> Dict[str, Any]:
    """Create one run and step it to completion. Returns a summary dict
    (including the failure, if the run crashed mid-way)."""
    label = spec["label"]
    t0 = time.time()
    try:
        state = engine.new_run(
            spec["traits"], spec["skills"],
            max_tries=spec["max_tries"], name=spec["name"],
            client_prompt=spec["client_prompt"],
            scripted_messages=spec["scripted_messages"],
            checkin_every=spec["checkin_every"],
            agent_model=spec["agent_model"], hr_model=spec["hr_model"],
            client_model=spec["client_model"], cv_model=spec["cv_model"])
        progress(f"[{label}] run {state['run_id']} created ({state['person']['name']}, "
                 f"skill: {spec['skills'][0]['name']})")
        while state["status"] == "running":
            state = engine.step(state, world)
            last = state["applications"][-1]
            progress(f"[{label}] #{last['i']}/{spec['max_tries']} "
                     f"{last['company_name']} — {last['decision'].upper()}")
        return {
            "label": label,
            "run_id": state["run_id"],
            "status": state["status"],
            "name": state["person"]["name"],
            "skill": spec["skills"][0]["name"],
            "client_prompt": spec["client_prompt"],
            "scripted_messages": spec["scripted_messages"],
            "applications": len(state["applications"]),
            "checkins": state.get("checkins", 0),
            "scripted_fired": [m["text"] for m in state["chat"] if m.get("scripted")],
            "seconds": round(time.time() - t0, 1),
            "transcript": engine.transcript_text(state),
            "error": None,
        }
    except Exception as exc:  # keep the rest of the batch alive
        progress(f"[{label}] FAILED: {exc}")
        return {"label": label, "run_id": None, "status": "error",
                "skill": spec["skills"][0]["name"] if spec.get("skills") else "",
                "client_prompt": spec.get("client_prompt", ""),
                "scripted_messages": spec.get("scripted_messages", {}),
                "applications": 0, "checkins": 0, "scripted_fired": [],
                "seconds": round(time.time() - t0, 1),
                "transcript": "", "error": str(exc)}


def run_batch(config: Dict[str, Any], world: Optional[Dict[str, Any]] = None,
              parallel: int = 1, progress=print) -> Dict[str, Any]:
    """Run the whole sweep; returns {batch_id, dir, results}. Also usable
    programmatically (the smoke test injects a stubbed LLM + fake world)."""
    world = world or world_module.load_world()
    specs = build_specs(config, world)
    if not specs:
        raise ValueError("config produced no runs (empty skills/client_prompts?)")
    for spec in specs:
        warning = check_reachability(spec)
        if warning:
            progress(f"[{spec['label']}] WARNING: {warning}")

    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    batch_dir = BATCHES_DIR / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    (batch_dir / "config.json").write_text(json.dumps(config, indent=2))
    progress(f"batch {batch_id}: {len(specs)} run(s), parallel={parallel} "
             f"-> {batch_dir}")

    if parallel > 1:
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            results = list(pool.map(lambda s: run_one(s, world, progress), specs))
    else:
        results = [run_one(s, world, progress) for s in specs]

    for r in results:
        if r["transcript"]:
            (batch_dir / f"transcript_{r['label']}.txt").write_text(r["transcript"])
    summary = [{k: v for k, v in r.items() if k != "transcript"} for r in results]
    (batch_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    progress("")
    progress(f"{'label':<24} {'status':<9} {'apps':>4} {'check-ins':>9}  run_id")
    for r in results:
        progress(f"{r['label']:<24} {r['status']:<9} {r['applications']:>4} "
                 f"{r['checkins']:>9}  {r['run_id'] or r['error']}")
    return {"batch_id": batch_id, "dir": str(batch_dir), "results": results}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("config", help="path to the batch config JSON")
    ap.add_argument("--parallel", type=int, default=1,
                    help="number of runs to execute concurrently (default 1)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the expanded run specs and exit without any LLM call")
    args = ap.parse_args()

    config = json.loads(Path(args.config).read_text())
    world = world_module.load_world()
    if args.dry_run:
        specs = build_specs(config, world)
        for spec in specs:
            warning = check_reachability(spec)
            print(json.dumps(spec, indent=2))
            if warning:
                print(f"  WARNING: {warning}")
        print(f"\n{len(specs)} run(s) would be started.")
        return

    outcome = run_batch(config, world, parallel=max(1, args.parallel))
    errors = [r for r in outcome["results"] if r["status"] == "error"]
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
