#!/usr/bin/env python3
"""
social-jira1 scenario + transcript viewer.

A tiny local Flask app to inspect a run's ground truth and conversation together:
the public task-pair goodness table, the private directional "who-likes-who" feelings
matrix, and the shared discussion - with discreet vs control side by side for the same
scenario (a fixed seed+type has identical goodness+feelings across framings; only the
confidentiality instruction differs).

Usage:
    python viewer.py            # then open http://localhost:5000
    python viewer.py --port 8000

Pick model / timestamp / scenario_type / seed / sampling, then tick the framings to show
as columns. The left panel shows the scenario (goodness table + feelings matrix + the
optimal / comfortable / realized matchings); the right shows each ticked framing's chat.
Toggle "deliberation" to reveal each turn's raw model output captured in
agent_trajectories.json (the dedicated gpt-oss hidden-CoT capture in agent_reasoning.json
came up empty for these runs, so that is the best available proxy).
"""

import argparse
import glob
import json
import re
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

HERE = Path(__file__).resolve().parent
OUTPUTS = (HERE / ".." / "outputs").resolve()
OUTPUT_PREFIX = "social_jira1_"

# Run dir names end with '__n<N>__t<T>__seed<seed>__s<sampling>'.
RUNDIR_RE = re.compile(r"__n(?P<n>\d+)__t(?P<t>\d+)__seed(?P<seed>\d+)__s(?P<sampling>\d+)$")

app = Flask(__name__)


# --------------------------------------------------------------------------- #
# Index: scan outputs/<set>/<ts>/runs/<model_label>/<framing>/<type>/<rundir>  #
# --------------------------------------------------------------------------- #
def short_model(set_dir_name: str) -> str:
    return set_dir_name[len(OUTPUT_PREFIX):] if set_dir_name.startswith(OUTPUT_PREFIX) else set_dir_name


def scan_index() -> dict:
    models: dict = {}
    if not OUTPUTS.is_dir():
        return {"models": models, "error": f"outputs dir not found: {OUTPUTS}"}

    for set_dir in sorted(OUTPUTS.iterdir()):
        if not set_dir.is_dir() or not set_dir.name.startswith(OUTPUT_PREFIX):
            continue
        display = short_model(set_dir.name)
        for ts_dir in sorted(set_dir.iterdir()):
            runs_root = ts_dir / "runs"
            if not runs_root.is_dir():
                continue
            for label_root in sorted(d for d in runs_root.iterdir() if d.is_dir()):
                model_label = label_root.name
                for rundir in glob.glob(str(label_root / "*" / "*" / "*")):
                    rp = Path(rundir)
                    m = RUNDIR_RE.search(rp.name)
                    if not m or not rp.is_dir():
                        continue
                    rel = rp.relative_to(label_root).parts[:-1]  # (framing, type)
                    models.setdefault(display, {"runs": []})["runs"].append({
                        "set_dir": set_dir.name,
                        "ts": ts_dir.name,
                        "model_label": model_label,
                        "framing": rel[0] if len(rel) >= 1 else "",
                        "type": rel[1] if len(rel) >= 2 else "",
                        "seed": int(m.group("seed")),
                        "sampling": int(m.group("sampling")),
                    })
    return {"models": models}


def find_run_dir(model, ts, framing, rtype, seed, sampling):
    md = scan_index()["models"].get(model)
    if not md:
        return None
    for r in md["runs"]:
        if (r["ts"] == ts and r["framing"] == framing and r["type"] == rtype
                and r["seed"] == int(seed) and r["sampling"] == int(sampling)):
            base = (OUTPUTS / r["set_dir"] / ts / "runs" / r["model_label"] / framing / rtype)
            hits = [p for p in glob.glob(str(base / f"*__seed{seed}__s{sampling}"))
                    if Path(p).is_dir() and RUNDIR_RE.search(Path(p).name)]
            if hits:
                return Path(hits[0])
    return None


# --------------------------------------------------------------------------- #
# Reasoning / deliberation from agent_trajectories.json                        #
# --------------------------------------------------------------------------- #
def _round_num(key: str) -> int:
    m = re.search(r"(\d+)$", key or "")
    return int(m.group(1)) if m else 0


def build_reasoning(run_dir: Path):
    """(agent, phase) -> [deliberation text per round, in round order].

    agent_trajectories.json nests agent -> iteration -> phase -> round -> trajectory ->
    step -> {reasoning, tools}. We join each round's non-empty step 'reasoning' text plus
    a compact note of any tool calls, giving the raw per-turn model output.
    """
    p = run_dir / "agent_trajectories.json"
    if not p.exists():
        return {}
    try:
        tj = json.loads(p.read_text())
    except Exception:
        return {}
    out: dict = {}
    for agent, iters in (tj or {}).items():
        for itk in sorted(iters.keys(), key=_round_num):
            for phase, rounds in (iters[itk] or {}).items():
                for rk in sorted(rounds.keys(), key=_round_num):
                    traj = (rounds[rk] or {}).get("trajectory", {}) or {}
                    parts = []
                    for sk in sorted(traj.keys(), key=_round_num):
                        step = traj[sk] or {}
                        r = (step.get("reasoning") or "").strip()
                        if r:
                            parts.append(r)
                        tools = step.get("tools") or []
                        for t in tools:
                            ts = str(t).strip()
                            # keep only a short signature of non-post tool calls
                            if ts and not ts.startswith("post_message"):
                                parts.append(f"[tool] {ts[:200]}")
                    out.setdefault((agent, phase), []).append("\n".join(parts).strip())
    return out


# --------------------------------------------------------------------------- #
# Load one run: scenario ground truth + aligned conversation                   #
# --------------------------------------------------------------------------- #
def load_scenario(run_dir: Path):
    p = run_dir / "scenario.json"
    return json.loads(p.read_text()) if p.exists() else None


def load_outcome(run_dir: Path):
    """Realized matching + headline metrics for this framing's run."""
    out = {"realized_matching": {}, "metrics": {}}
    fs = run_dir / "final_summary.json"
    if fs.exists():
        d = json.loads(fs.read_text())
        out["realized_matching"] = d.get("realized_matching") or {}
    mp = run_dir / "metrics.json"
    if mp.exists():
        out["metrics"] = json.loads(mp.read_text())
    return out


def load_messages(run_dir: Path):
    """Ordered chat bubbles from the shared blackboard, with deliberation attached."""
    bb_path = run_dir / "blackboards.json"
    if not bb_path.exists():
        return {"participants": [], "turn_order": [], "context": None, "messages": []}
    blackboards = json.loads(bb_path.read_text())
    bb = blackboards[0] if blackboards else {"participants": [], "events": []}
    participants = bb.get("participants", [])

    turn_order = participants[:]
    rc = run_dir / "run_config.json"
    if rc.exists():
        turn_order = json.loads(rc.read_text()).get("agent_turn_order") or turn_order
    turn_pos = {a: i for i, a in enumerate(turn_order)}

    reasoning_idx = build_reasoning(run_dir)
    PHASE_RANK = {"planning": 0, "execution": 1}

    seen_in_phase: dict = {}
    context_msg = None
    messages = []
    for ev in bb.get("events", []):
        agent = ev.get("agent")
        kind = ev.get("kind")
        payload = ev.get("payload", {}) or {}
        if kind == "context":
            context_msg = payload.get("message")
            continue
        phase = payload.get("phase") or "planning"
        key = (phase, agent)
        rnd = seen_in_phase.get(key, 0)
        seen_in_phase[key] = rnd + 1

        extra = {}
        if kind == "communication":
            text = payload.get("content", "")
            mkind = "msg"
        elif kind == "action_executed":
            params = payload.get("action_params", {}) or {}
            task_id = params.get("task_id") or params.get("action")
            text = f"{params.get('action', 'action')} → {task_id}"
            mkind = "action"
            extra = {"task_id": task_id, "status": payload.get("result_status")}
        else:
            text = json.dumps(payload)[:400]
            mkind = "other"

        rlist = reasoning_idx.get((agent, phase), [])
        deliberation = rlist[rnd] if rnd < len(rlist) else ""

        messages.append({
            "agent": agent, "kind": mkind, "text": text,
            "phase": phase, "phase_rank": PHASE_RANK.get(phase, 9),
            "round": rnd, "pos": turn_pos.get(agent, 99),
            "deliberation": deliberation, **extra,
        })

    messages.sort(key=lambda m: (m["phase_rank"], m["round"], m["pos"]))
    return {"participants": participants, "turn_order": turn_order,
            "context": context_msg, "messages": messages}


def load_cell(model, ts, rtype, seed, sampling, framings):
    """Scenario panel (shared) + one conversation column per requested framing."""
    scenario = None
    columns = []
    for framing in framings:
        rd = find_run_dir(model, ts, framing, rtype, seed, sampling)
        if not rd:
            columns.append({"framing": framing, "ok": False, "error": "run not found"})
            continue
        if scenario is None:
            scenario = load_scenario(rd)
        chat = load_messages(rd)
        outcome = load_outcome(rd)
        columns.append({"framing": framing, "ok": True, "run_dir": str(rd),
                        **chat, **outcome})
    return {"scenario": scenario, "columns": columns,
            "dims": {"model": model, "ts": ts, "type": rtype,
                     "seed": seed, "sampling": sampling}}


# --------------------------------------------------------------------------- #
# Routes                                                                       #
# --------------------------------------------------------------------------- #
@app.route("/")
def home():
    return send_from_directory(HERE, "index.html")


@app.route("/api/index")
def api_index():
    return jsonify(scan_index())


@app.route("/api/cell")
def api_cell():
    a = request.args
    try:
        seed = int(a["seed"])
        sampling = int(a["sampling"])
    except (KeyError, ValueError):
        return jsonify({"error": "seed/sampling required"}), 400
    framings = [f for f in (a.get("framings", "").split(",")) if f]
    return jsonify(load_cell(a.get("model", ""), a.get("timestamp", ""),
                             a.get("type", ""), seed, sampling, framings))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    print(f"social-jira1 viewer → http://{args.host}:{args.port}  (Ctrl-C to stop)")
    print(f"Scanning outputs at: {OUTPUTS}")
    app.run(host=args.host, port=args.port, debug=False)
