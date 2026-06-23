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


def _reasoning_from_cot(run_dir: Path):
    """(agent, phase) -> [CoT text per round] from agent_reasoning.json.

    This is the real chain-of-thought: gpt-oss's analysis channel, captured per turn as
    agent -> iteration -> phase -> round -> {step -> {reasoning_content, content}}. We join
    each round's non-empty reasoning_content across steps. Returns {} when the file is
    missing or holds no actual reasoning (older runs where capture came up empty).
    """
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
    """(agent, phase) -> [text per round] reconstructed from agent_trajectories.json.

    Fallback proxy when no real CoT was captured: agent_trajectories.json nests agent ->
    iteration -> phase -> round -> trajectory -> step -> {reasoning, tools}. We join each
    round's non-empty step 'reasoning' (the raw per-turn output, which for gpt-oss often
    just restates the posted message) plus a compact note of any non-post tool calls.
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


def build_reasoning(run_dir: Path):
    """((agent, phase) -> [text per round], source).

    Prefer the real chain-of-thought in agent_reasoning.json (gpt-oss analysis channel);
    fall back to the per-turn raw output from agent_trajectories.json when no CoT was
    captured. ``source`` is 'cot', 'trajectory', or 'none' so the UI can label it honestly.
    """
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
    return json.loads(p.read_text()) if p.exists() else None


def _planning_turns(run_dir: Path):
    """Ordered list of (agent, round0) for each planning turn, from agent_turns.json.

    agent_turns.json is the chronological turn log; each planning turn carries the true
    1-based ``planning_round``. Returns None if unavailable so callers can fall back.
    """
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
    """Per-event true planning round (0-based), aligning blackboard posts to turns.

    A single agent turn can emit several blackboard posts, so per-agent post counting
    mislabels rounds. Instead we group consecutive same-agent planning posts (= one turn's
    posts, since turns are sequential) and consume turns in order, skipping turns whose
    agent posted nothing. Returns a list parallel to `events` (None for non-planning).
    """
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
        while ti < len(turns) and turns[ti][0] != agent:  # skip turns that produced no post
            ti += 1
        rnd = turns[ti][1] if ti < len(turns) else None
        if ti < len(turns):
            ti += 1
        for k in range(i, j):
            rounds[k] = rnd
        i = j
    return rounds


def load_agent_prompt(run_dir: Path, agent: str = None):
    """Full system + user prompt for one agent's first turn.

    agent_prompts.json is a time-ordered list of per-turn records, each with
    agent_name / phase / round / system_prompt / user_prompt. We return the
    earliest record for `agent` (default: the very first record = agent[0],
    the first agent's planning round 1) so the panel shows exactly what that
    agent was handed verbatim.
    """
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
    """Normalize a 'A & B' pair string to canonical 'sorted(A,B) joined by &'.

    Scenario goodness keys are already alphabetical ('Jeanene & Layla'), but an agent's
    private ballot might write the two names in either order; normalizing both sides lets
    a vote match the canonical pair label. Returns None for 'none'/blank/malformed.
    """
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
    """Mutate `messages`, attaching an evolving private-ballot snapshot to each one.

    Each agent's round-r (0-based, reconstructed) planning post "casts" that agent's
    ``round_{r+1}`` ballot. We carry a persistent per-agent current vote that *moves* when
    the agent votes again next round, and stamp a snapshot of every agent's current vote
    onto each message (``vote_state``: agent -> {task: canonical_pair}) plus ``voted_now``
    (the agent whose ballot just changed at this message, for highlighting).

    Returns column-level meta: the canonical pair universe + task order + the optimal /
    comfortable target pairs (so the client can outline those cells).
    """
    sc = scenario or {}
    tasks = sc.get("tasks") or []
    goodness = sc.get("goodness") or {}
    pairs = list(goodness.get(tasks[0], {}).keys()) if tasks else []
    canon = {_norm_pair(p): p for p in pairs}

    def canonical(v):
        return canon.get(_norm_pair(v))

    current: dict = {}  # agent -> {task: canonical_pair}
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
                if current.get(agent) != cur:  # highlight only when the ballot actually changes
                    voted_now = agent
                current[agent] = cur
        m["vote_state"] = {a: dict(v) for a, v in current.items()}
        m["voted_now"] = voted_now

    return {"pairs": pairs, "tasks": tasks,
            "optimal": sc.get("optimal_matching") or {},
            "comfortable": sc.get("comfortable_matching") or {}}


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
    reasoning_idx, reasoning_source = build_reasoning(run_dir)

    events = bb.get("events", [])
    # True planning round per event from the turn log (a turn may post >1 message, so
    # naive per-agent post counting mislabels rounds). Falls back to counting if absent.
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

    # Keep the blackboard's own (chronological) event order so post references
    # like "[3]/[4]/[6]" line up with the turns that actually preceded a message.
    # (A prior version re-sorted by fixed turn position, which made the
    # round-robin's first speaker appear to cite posts that came after it.)
    return {"participants": participants, "turn_order": turn_order,
            "context": context_msg, "messages": messages,
            "reasoning_source": reasoning_source}


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
        # Private ballots evolve per framing (discreet vs control differ), so attach
        # per-message vote snapshots against this run's own scenario ground truth.
        vote_meta = attach_vote_states(chat["messages"], load_votes(rd), scenario)
        # agent[0] = first agent in the turn order; show its full verbatim prompt.
        prompt0 = load_agent_prompt(rd, (chat.get("turn_order") or [None])[0])
        columns.append({"framing": framing, "ok": True, "run_dir": str(rd),
                        "prompt0": prompt0, "votes": vote_meta, **chat, **outcome})
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
