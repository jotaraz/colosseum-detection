#!/usr/bin/env python3
"""
social-jira2 scenario + transcript viewer.

A tiny local Flask app to inspect a run's ground truth and conversation together: the
role-driven public goodness table (numbers, the ground truth — agents only ever saw the
roles), the private directional "who-dislikes-whom" feelings matrix, the roster
(profession + seniority) and task requirements, and the shared discussion — with the
**personalities** side by side for the same scenario (a fixed seed + scenario_type +
feelings_variant has identical roster / tasks / goodness / feelings across personalities;
only the assistant's WHO-block personality line differs).

Usage:
    python viewer.py            # then open http://localhost:5001
    python viewer.py --port 8000

Pick model / timestamp / feelings_variant / scenario_type / seed / sample, then tick the
personalities to show as columns. The left panel shows the scenario (roster, task
requirements, numeric goodness table, feelings matrix, and the optimal / comfortable /
realized matchings); the right shows each ticked personality's chat. Toggle "deliberation"
to reveal each turn's chain-of-thought (agent_reasoning.json) or, if none was captured, the
raw per-turn output from agent_trajectories.json.

This mirrors the social_jira1 viewer. When a run has been scored by ``judge.py``, ticking
"show judge phenomena" overlays the phenomenon-judge results (``judge_results.json``): a
per-run summary banner plus each flagged phenomenon (with its spans + note) anchored under the
turn it was found in. Until that file exists the overlay shows "no judge results".
"""

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path

# Run under whatever interpreter has Flask. When launched with a bare `python`
# that lacks Flask (e.g. a system/pyenv shim), re-exec into the repo's `.venv`
# if one is found by walking up from this file. Keeps `python viewer.py` working
# regardless of which machine / interpreter it's started with.
try:
    from flask import Flask, jsonify, request, send_from_directory
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
    os.execv(str(_venv_py), [str(_venv_py), *sys.argv])

HERE = Path(__file__).resolve().parent
OUTPUTS = (HERE / ".." / "outputs").resolve()
OUTPUT_PREFIX = "social_jira2_"

# Run dir names end with '__n<N>__t<T>__seed<seed>__s<sampling>'.
RUNDIR_RE = re.compile(r"__n(?P<n>\d+)__t(?P<t>\d+)__seed(?P<seed>\d+)__s(?P<sampling>\d+)$")

app = Flask(__name__)


# --------------------------------------------------------------------------- #
# Index: scan outputs/<set>/<ts>/runs/<model>/<variant>/<type>/<pers>/<rundir> #
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
        # A run-set is laid out as <set>/<timestamp>/runs/... (normal sweeps) or, for
        # resume/merged outputs, directly as <set>/runs/... (no timestamp level).
        ts_candidates = []
        if (set_dir / "runs").is_dir():
            ts_candidates.append(("(merged)", set_dir / "runs"))
        for ts_dir in sorted(set_dir.iterdir()):
            if ts_dir.is_dir() and (ts_dir / "runs").is_dir():
                ts_candidates.append((ts_dir.name, ts_dir / "runs"))
        for ts_name, runs_root in ts_candidates:
            for label_root in sorted(d for d in runs_root.iterdir() if d.is_dir()):
                model_label = label_root.name
                # Run dirs live at either depth-4 (older layout, no setup axis)
                #   <variant>/<type>/<personality>/<rundir>
                # or depth-5 (with the setup axis)
                #   <setup>/<variant>/<type>/<personality>/<rundir>
                candidates = (glob.glob(str(label_root / "*" / "*" / "*" / "*"))
                              + glob.glob(str(label_root / "*" / "*" / "*" / "*" / "*")))
                for rundir in candidates:
                    rp = Path(rundir)
                    m = RUNDIR_RE.search(rp.name)
                    if not m or not rp.is_dir():
                        continue
                    anc = rp.relative_to(label_root).parts[:-1]  # dirs between label_root and rundir
                    if len(anc) >= 4:
                        setup, variant, rtype, personality = anc[-4], anc[-3], anc[-2], anc[-1]
                    elif len(anc) == 3:
                        setup, variant, rtype, personality = "base", anc[0], anc[1], anc[2]
                    else:
                        continue
                    models.setdefault(display, {"runs": []})["runs"].append({
                        "set_dir": set_dir.name,
                        "ts": ts_name,
                        "model_label": model_label,
                        "setup": setup,
                        "feelings_variant": variant,
                        "type": rtype,
                        "personality": personality,
                        "seed": int(m.group("seed")),
                        "sampling": int(m.group("sampling")),
                        "run_dir": str(rp),
                    })
    return {"models": models}


def find_run_dir(model, ts, setup, feelings_variant, rtype, personality, seed, sampling):
    md = scan_index()["models"].get(model)
    if not md:
        return None
    for r in md["runs"]:
        if (r["ts"] == ts and r.get("setup", "base") == setup
                and r["feelings_variant"] == feelings_variant and r["type"] == rtype
                and r["personality"] == personality
                and r["seed"] == int(seed) and r["sampling"] == int(sampling)):
            p = Path(r["run_dir"])
            if p.is_dir():
                return p
    return None


# --------------------------------------------------------------------------- #
# Reasoning / deliberation                                                     #
# --------------------------------------------------------------------------- #
def _round_num(key: str) -> int:
    m = re.search(r"(\d+)$", key or "")
    return int(m.group(1)) if m else 0


def _reasoning_from_cot(run_dir: Path):
    """(agent, phase) -> [CoT text per round] from agent_reasoning.json (gpt-oss analysis)."""
    p = run_dir / "agent_reasoning.json"
    if not p.exists():
        return {}
    try:
        rj = json.loads(p.read_text())
    except Exception:
        return {}
    out: dict = {}
    any_text = False
    for agent, iters in (rj or {}).items():
        for itk in sorted(iters.keys(), key=_round_num):
            for phase, rounds in (iters[itk] or {}).items():
                for rk in sorted(rounds.keys(), key=_round_num):
                    steps = rounds[rk] or {}
                    parts = []
                    for sk in sorted(steps.keys(), key=_round_num):
                        r = ((steps[sk] or {}).get("reasoning_content") or "").strip()
                        if r:
                            parts.append(r)
                    text = "\n\n".join(parts).strip()
                    if text:
                        any_text = True
                    out.setdefault((agent, phase), []).append(text)
    return out if any_text else {}


def _reasoning_from_trajectories(run_dir: Path):
    """(agent, phase) -> [text per round] reconstructed from agent_trajectories.json (proxy)."""
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
                        for t in (step.get("tools") or []):
                            ts = str(t).strip()
                            if ts and not ts.startswith("post_message"):
                                parts.append(f"[tool] {ts[:200]}")
                    out.setdefault((agent, phase), []).append("\n".join(parts).strip())
    return out


def build_reasoning(run_dir: Path):
    """((agent, phase) -> [text per round], source in {'cot','trajectory','none'})."""
    cot = _reasoning_from_cot(run_dir)
    if cot:
        return cot, "cot"
    traj = _reasoning_from_trajectories(run_dir)
    return traj, ("trajectory" if traj else "none")


# --------------------------------------------------------------------------- #
# Load one run: scenario ground truth + aligned conversation                   #
# --------------------------------------------------------------------------- #
def load_scenario(run_dir: Path):
    p = run_dir / "scenario.json"
    if not p.exists():
        return None
    sc = json.loads(p.read_text())
    # Compat shim for the (tie-aware) optimal set: expose a single representative
    # `optimal_matching` for outlining, plus keep the full G*-set for display.
    oms = sc.get("optimal_matchings") or []
    sc["optimal_matching"] = oms[0] if oms else {}
    sc["num_optimal_matchings"] = len(oms)
    return sc


def _planning_turns(run_dir: Path):
    """Ordered list of (agent, round0) for each planning turn, from agent_turns.json."""
    p = run_dir / "agent_turns.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
    except Exception:
        return None
    turns = []
    for e in data or []:
        if e.get("phase") == "planning":
            r = e.get("planning_round")
            turns.append((e.get("agent"), (r - 1) if isinstance(r, int) and r > 0 else 0))
    return turns or None


def _align_planning_rounds(events, turns):
    """Per-event true planning round (0-based), aligning blackboard posts to turns."""
    rounds = [None] * len(events)
    if not turns:
        return rounds

    def is_planning_comm(ev):
        return ev.get("kind") == "communication" and ((ev.get("payload", {}) or {}).get("phase") or "planning") == "planning"

    ti, i, n = 0, 0, len(events)
    while i < n:
        if not is_planning_comm(events[i]):
            i += 1
            continue
        agent = events[i].get("agent")
        j = i
        while j < n and is_planning_comm(events[j]) and events[j].get("agent") == agent:
            j += 1
        while ti < len(turns) and turns[ti][0] != agent:
            ti += 1
        rnd = turns[ti][1] if ti < len(turns) else None
        if ti < len(turns):
            ti += 1
        for k in range(i, j):
            rounds[k] = rnd
        i = j
    return rounds


def load_agent_prompt(run_dir: Path, agent: str = None):
    """Full system + user prompt for one agent's first turn (verbatim, from agent_prompts.json)."""
    p = run_dir / "agent_prompts.json"
    if not p.exists():
        return None
    try:
        recs = json.loads(p.read_text())
    except Exception:
        return None
    if not recs:
        return None
    rec = next((r for r in recs if r.get("agent_name") == agent), None) if agent else None
    rec = rec or recs[0]
    return {
        "agent": rec.get("agent_name"),
        "phase": rec.get("phase"),
        "round": rec.get("round"),
        "system_prompt": rec.get("system_prompt", ""),
        "user_prompt": rec.get("user_prompt", ""),
    }


def _norm_pair(s):
    """Normalize a 'A & B' pair string to canonical 'sorted(A,B) joined by &'."""
    if not s:
        return None
    s = str(s).strip()
    if s.lower() in ("none", "-", "—", ""):
        return None
    parts = [p.strip() for p in s.split("&") if p.strip()]
    if len(parts) != 2:
        return None
    return " & ".join(sorted(parts))


def load_votes(run_dir: Path):
    """Raw private per-round ballots: agent -> round_N -> {assignment:{task:pair}, raw, reasoning[]}."""
    p = run_dir / "agent_votes.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def attach_vote_states(messages, votes, scenario):
    """Attach an evolving private-ballot snapshot to each message (see jira1 viewer)."""
    sc = scenario or {}
    tasks = sc.get("tasks") or []
    goodness = sc.get("goodness") or {}
    pairs = list(goodness.get(tasks[0], {}).keys()) if tasks else []
    canon = {_norm_pair(p): p for p in pairs}

    def canonical(v):
        return canon.get(_norm_pair(v))

    current: dict = {}
    for m in messages:
        voted_now = None
        if m.get("phase") == "planning":
            agent = m.get("agent")
            rnd = m.get("round") or 0
            av = (votes.get(agent) or {}).get(f"round_{rnd + 1}")
            if av:
                assign = av.get("assignment") or {}
                cur = {}
                for t in tasks:
                    cp = canonical(assign.get(t))
                    if cp:
                        cur[t] = cp
                if current.get(agent) != cur:
                    voted_now = agent
                current[agent] = cur
        m["vote_state"] = {a: dict(v) for a, v in current.items()}
        m["voted_now"] = voted_now

    return {"pairs": pairs, "tasks": tasks,
            "optimal": sc.get("optimal_matching") or {},
            "comfortable": sc.get("comfortable_matching") or {}}


def load_judge(run_dir: Path):
    """Phenomenon-judge results, from ``judge_results.json`` (see ``judge.py``).

    Returns None until the file exists. Flattens each turn's ``present_phenomena`` into flat
    "incidents" the frontend can anchor to a chat bubble by (agent, phase, viewer-round) and
    render inline, plus per-run phenomenon counts for the summary banner.

    Round convention: the judge numbers planning rounds 1-based (from ``tool_events``'
    ``planning_round``) while the viewer numbers them 0-based, and execution is a single merged
    turn that we anchor to the announcement bubble (viewer round 0)."""
    p = run_dir / "judge_results.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
    except Exception:
        return None

    incidents: list = []
    counts: dict = {}
    for t in d.get("turns") or []:
        agent = t.get("agent")
        phase = t.get("phase") or "planning"
        jround = t.get("round")
        if phase == "planning" and isinstance(jround, int) and jround > 0:
            vround = jround - 1
        else:
            vround = 0
        for ph in t.get("present_phenomena") or []:
            name = ph.get("phenomenon") or "?"
            counts[name] = counts.get(name, 0) + 1
            incidents.append({
                "agent": agent, "phase": phase, "vround": vround,
                "turn_index": t.get("turn_index"),
                "phenomenon": name,
                "spans": ph.get("spans") or [],
                "note": ph.get("note") or "",
            })
    n_errors = sum(1 for t in (d.get("turns") or []) if t.get("parse_error"))

    return {
        "schema": "phenomena",
        "judge_model": d.get("judge_model"),
        "num_turns": d.get("num_turns") or len(d.get("turns") or []),
        "total_flags": len(incidents),
        "counts": counts,
        "n_errors": n_errors,
        "incidents": incidents,
    }


def load_outcome(run_dir: Path):
    """Realized matching + headline metrics for this personality's run."""
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
    reasoning_idx, reasoning_source = build_reasoning(run_dir)

    events = bb.get("events", [])
    planning_rounds = _align_planning_rounds(events, _planning_turns(run_dir))

    seen_in_phase: dict = {}
    context_msg = None
    messages = []
    for idx, ev in enumerate(events):
        agent = ev.get("agent")
        kind = ev.get("kind")
        payload = ev.get("payload", {}) or {}
        if kind == "context":
            context_msg = payload.get("message")
            continue
        phase = payload.get("phase") or "planning"
        if phase == "planning" and planning_rounds[idx] is not None:
            rnd = planning_rounds[idx]
        else:
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
            "phase": phase, "round": rnd,
            "deliberation": deliberation, **extra,
        })

    return {"participants": participants, "turn_order": turn_order,
            "context": context_msg, "messages": messages,
            "reasoning_source": reasoning_source}


def load_cell(model, ts, setup, feelings_variant, rtype, seed, sampling, personalities):
    """Scenario panel (shared) + one conversation column per requested personality."""
    scenario = None
    columns = []
    for personality in personalities:
        rd = find_run_dir(model, ts, setup, feelings_variant, rtype, personality, seed, sampling)
        if not rd:
            columns.append({"personality": personality, "ok": False, "error": "run not found"})
            continue
        if scenario is None:
            scenario = load_scenario(rd)
        chat = load_messages(rd)
        outcome = load_outcome(rd)
        vote_meta = attach_vote_states(chat["messages"], load_votes(rd), scenario)
        prompt0 = load_agent_prompt(rd, (chat.get("turn_order") or [None])[0])
        columns.append({"personality": personality, "ok": True, "run_dir": str(rd),
                        "prompt0": prompt0, "votes": vote_meta,
                        "judge": load_judge(rd), **chat, **outcome})
    return {"scenario": scenario, "columns": columns,
            "dims": {"model": model, "ts": ts, "setup": setup,
                     "feelings_variant": feelings_variant,
                     "type": rtype, "seed": seed, "sampling": sampling}}


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
    personalities = [p for p in (a.get("personalities", "").split(",")) if p]
    return jsonify(load_cell(a.get("model", ""), a.get("timestamp", ""),
                             a.get("setup", "base"),
                             a.get("feelings_variant", ""), a.get("type", ""),
                             seed, sampling, personalities))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5001)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    print(f"social-jira2 viewer → http://{args.host}:{args.port}  (Ctrl-C to stop)")
    print(f"Scanning outputs at: {OUTPUTS}")
    app.run(host=args.host, port=args.port, debug=False)
