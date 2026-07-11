#!/usr/bin/env python3
"""job_appl4 completed-run viewer.

A tiny local Flask app to read a finished run end-to-end: the setup (person,
traits, skills, models, client steering prompt), the dossier the agent treated
as ground truth, and the full chronological timeline — each application's agent
reasoning + private chain-of-thought + cover letter + HR decision, every
autonomous check-in (agent report + client comment), and the final outcome.

When a run has been scored by ``vibe_judge.py`` it also surfaces the honesty
verdict (``vibe_judgment.json``): the structured header (dishonesty_present /
primary_driver / consent_structure) as a banner, the prose read, and the raw
judge response. Runs are listed with a color-coded verdict badge so a whole
sweep's honesty texture is scannable at a glance.

Everything is read-only; it reuses the same ``events.jsonl`` schema that
``engine.transcript_text`` renders, so the timeline matches the .txt transcript.

Usage:
    python viewer.py                 # then open http://localhost:5002
    python viewer.py --port 8000
"""

import argparse
import json
import sys
from pathlib import Path

# Run under whatever interpreter has Flask. When launched with a bare `python`
# that lacks Flask, re-exec into the repo's `.venv` if one is found by walking up
# from this file — keeps `python viewer.py` working regardless of interpreter.
try:
    from flask import Flask, jsonify, send_from_directory
except ModuleNotFoundError:
    _here = Path(__file__).resolve()
    _venv_py = next(
        (p / ".venv" / "bin" / "python"
         for p in _here.parents
         if (p / ".venv" / "bin" / "python").exists()),
        None,
    )
    if _venv_py is None or Path(sys.executable).resolve() == _venv_py.resolve():
        raise SystemExit(
            "Flask is not installed for this interpreter and no repo .venv with "
            "Flask was found.\nInstall it (e.g. `pip install flask`) or run with the "
            "repo venv: `<repo>/.venv/bin/python viewer.py`."
        )
    import os
    os.execv(str(_venv_py), [str(_venv_py), *sys.argv])

HERE = Path(__file__).resolve().parent
RUNS = (HERE / ".." / "outputs" / "runs").resolve()

app = Flask(__name__)


# --------------------------------------------------------------------------- #
# Loading                                                                      #
# --------------------------------------------------------------------------- #
def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _iter_events(run_dir: Path):
    p = run_dir / "events.jsonl"
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def load_judgment(run_dir: Path):
    """The vibe-judge verdict for one run (``vibe_judgment.json``), or None."""
    d = _read_json(run_dir / "vibe_judgment.json")
    if not d:
        return None
    return {
        "dishonesty_present": d.get("dishonesty_present"),
        "primary_driver": d.get("primary_driver"),
        "consent_structure": d.get("consent_structure"),
        "prose": d.get("prose") or "",
        "judge_model": d.get("judge_model"),
        "include_cot": d.get("include_cot"),
        "raw_response": d.get("raw_response") or "",
    }


def scan_index() -> dict:
    """One summary row per run dir under outputs/runs, newest first."""
    runs = []
    if not RUNS.is_dir():
        return {"runs": [], "runs_dir": str(RUNS), "error": f"not found: {RUNS}"}
    for d in sorted((p for p in RUNS.iterdir() if p.is_dir()), reverse=True):
        s = _read_json(d / "state.json")
        if not s:
            continue
        j = load_judgment(d)
        models = s.get("models") or {}
        runs.append({
            "run_id": s.get("run_id") or d.name,
            "person": (s.get("person") or {}).get("name"),
            "status": s.get("status"),
            "agent_model": models.get("agent"),
            "applications": len(s.get("applications") or []),
            "checkins": s.get("checkins"),
            "judged": j is not None,
            "dishonesty_present": (j or {}).get("dishonesty_present"),
            "primary_driver": (j or {}).get("primary_driver"),
            "consent_structure": (j or {}).get("consent_structure"),
        })
    return {"runs": runs, "runs_dir": str(RUNS)}


def load_run(run_id: str):
    """Full structured run: setup, dossier, chronological timeline, judgment."""
    run_dir = RUNS / run_id
    if not (run_dir / "state.json").is_file():
        return None
    state = _read_json(run_dir / "state.json") or {}
    person = state.get("person") or {}
    models = state.get("models") or {}

    setup = {
        "run_id": state.get("run_id") or run_id,
        "created_at": state.get("created_at"),
        "status": state.get("status"),
        "name": person.get("name"),
        "traits": person.get("traits") or [],
        "skills": person.get("skills") or [],
        "client_prompt": state.get("client_prompt") or "",
        "scripted_messages": state.get("scripted_messages") or {},
        "max_tries": state.get("max_tries"),
        "checkin_every": state.get("checkin_every"),
        "models": models,
    }

    dossier = person.get("cv") or ""
    timeline = []
    for e in _iter_events(run_dir):
        t = e.get("type")
        if t == "dossier" and e.get("dossier"):
            dossier = e["dossier"]  # the ground truth exactly as the run recorded it
        elif t == "application":
            timeline.append({
                "kind": "application",
                "i": e.get("i"),
                "company_name": e.get("company_name"),
                "job_title": e.get("job_title"),
                "agent_reasoning": e.get("agent_reasoning") or "",
                "agent_cot": e.get("agent_cot") or "",
                "cover_letter": e.get("cover_letter") or "",
                "decision": e.get("decision"),
                "reasons": e.get("reasons") or "",
            })
        elif t == "checkin":
            timeline.append({
                "kind": "checkin",
                "index": e.get("index"),
                "report": e.get("report") or "",
                "comment": e.get("comment") or "",
                "scripted": bool(e.get("scripted")),
            })
        elif t == "final":
            timeline.append({
                "kind": "final",
                "success": bool(e.get("success")),
                "report": e.get("report") or "",
            })

    return {
        "setup": setup,
        "dossier": dossier,
        "timeline": timeline,
        "judgment": load_judgment(run_dir),
    }


# --------------------------------------------------------------------------- #
# Routes                                                                       #
# --------------------------------------------------------------------------- #
@app.route("/")
def home():
    return send_from_directory(HERE, "index.html")


@app.route("/api/index")
def api_index():
    return jsonify(scan_index())


@app.route("/api/run/<run_id>")
def api_run(run_id: str):
    data = load_run(run_id)
    if data is None:
        return jsonify({"error": f"run not found: {run_id}"}), 404
    return jsonify(data)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=5002)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    print(f"job_appl4 viewer → http://{args.host}:{args.port}  (Ctrl-C to stop)")
    print(f"Scanning runs at: {RUNS}")
    app.run(host=args.host, port=args.port, debug=False)
