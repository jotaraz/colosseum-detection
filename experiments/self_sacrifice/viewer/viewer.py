#!/usr/bin/env python3
"""
Self-sacrifice transcript viewer.

A tiny local Flask app to compare blackboard conversations across framings,
side by side, rendered like group chats.

Usage:
    python viewer.py            # then open http://localhost:5000
    python viewer.py --port 8000

Pick an LLM, timestamp, seed, sampling id and type, then tick one or more
framing buttons. Each ticked framing is shown as its own group-chat column.
Columns are aligned row-by-row by turn (phase -> round -> agent turn order),
so the same agent's turn lines up horizontally across framings.

Global task priorities are shown at the top of every column; each agent's
private cost vector is shown on that agent's first message bubble.
"""

import argparse
import glob
import json
import os
import re
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

HERE = Path(__file__).resolve().parent
OUTPUTS = (HERE / ".." / "outputs").resolve()
# Matches every experiment variant: self_sacrifice_n6_*, self_sacrifice_obvious_n3_*, …
OUTPUT_PREFIX = "self_sacrifice_"

# A run dir name always ends with '__n<N>__seed<seed>__s<sampling>'. The tokens
# before that (model_label, framing, type, and — depending on the variant — an
# env/table token and a scenario token such as 'complete') vary, so framing/type
# and the optional env are read from the directory tree, not the name.
RUNDIR_RE = re.compile(r"__n(?P<n>\d+)__seed(?P<seed>\d+)__s(?P<sampling>\d+)$")

app = Flask(__name__)


# --------------------------------------------------------------------------- #
# Index: scan the outputs tree so the dropdowns reflect what's actually there. #
# --------------------------------------------------------------------------- #
def short_model(set_dir_name: str) -> str:
    """self_sacrifice_n6_gptoss_120b -> n6_gptoss_120b;
    self_sacrifice_obvious_n3_llama33_70b -> obvious_n3_llama33_70b"""
    return set_dir_name[len(OUTPUT_PREFIX):] if set_dir_name.startswith(OUTPUT_PREFIX) else set_dir_name


# A set dir is self_sacrifice_<variant>_n<N>_<model> (e.g. ..._obvious_n3_llama33_70b,
# ..._veryego_n3_llama33_70b). Variants that share the same n/model describe the
# *same* scenarios (identical agents/costs per seed) and differ only in which
# framings they ran, so we merge them into one model entry keyed by n<N>_<model>.
VARIANT_RE = re.compile(r"^self_sacrifice_(?P<variant>[^_]+)_n(?P<n>\d+)_(?P<model>.+)$")


def model_group(set_dir_name: str):
    """(display_key, variant) for a set dir. Variants of the same n/model merge:
        self_sacrifice_obvious_n3_llama33_70b -> ('n3_llama33_70b', 'obvious')
        self_sacrifice_veryego_n3_llama33_70b -> ('n3_llama33_70b', 'veryego')
    Anything that doesn't fit the variant pattern groups under its own short name."""
    m = VARIANT_RE.match(set_dir_name)
    if m:
        return f"n{m.group('n')}_{m.group('model')}", m.group("variant")
    return short_model(set_dir_name), ""


def scan_index() -> dict:
    """
    Build a flat index of everything on disk:
        { models: { <display_key>: { runs: [ {
            set_dir, ts, variant, model_label, framing, type, env, seed, sampling
        }, ... ] } } }

    A model entry merges every variant set dir that shares its n/model (see
    model_group); each run carries its own provenance (set_dir/ts/variant) so a
    column can be loaded no matter which variant it came from. 'env' is the extra
    table/scenario sub-dir some variants use (framing/type/<table>/); "" otherwise.
    """
    models = {}
    if not OUTPUTS.is_dir():
        return {"models": models, "error": f"outputs dir not found: {OUTPUTS}"}

    for set_dir in sorted(OUTPUTS.iterdir()):
        if not set_dir.is_dir() or not set_dir.name.startswith(OUTPUT_PREFIX):
            continue
        display, variant = model_group(set_dir.name)
        for ts_dir in sorted(set_dir.iterdir()):
            runs_root = ts_dir / "runs"
            if not runs_root.is_dir():
                continue
            # exactly one model_label dir under runs/
            label_dirs = [d for d in runs_root.iterdir() if d.is_dir()]
            if not label_dirs:
                continue
            model_label = label_dirs[0].name
            label_root = label_dirs[0]
            # rundirs live at runs/<model_label>/<framing>/<type>/<rundir> (n6) or
            # runs/<model_label>/<framing>/<type>/<env>/<rundir> (obvious_*/veryego_*);
            # glob both depths and let RUNDIR_RE pick out the actual run dirs.
            candidates = (glob.glob(str(label_root / "*" / "*" / "*")) +
                          glob.glob(str(label_root / "*" / "*" / "*" / "*")))
            for rundir in candidates:
                rp = Path(rundir)
                m = RUNDIR_RE.search(rp.name)
                if not m or not rp.is_dir():
                    continue
                # path parts between <model_label> and the run dir => framing/type[/env]
                rel = rp.relative_to(label_root).parts[:-1]
                models.setdefault(display, {"runs": []})["runs"].append({
                    "set_dir": set_dir.name,
                    "ts": ts_dir.name,
                    "variant": variant,
                    "model_label": model_label,
                    "framing": rel[0] if len(rel) >= 1 else "",
                    "type": rel[1] if len(rel) >= 2 else "",
                    "env": rel[2] if len(rel) >= 3 else "",
                    "seed": int(m.group("seed")),
                    "sampling": int(m.group("sampling")),
                })
    return {"models": models}


# --------------------------------------------------------------------------- #
# Parsing a single run dir into chat data.                                     #
# --------------------------------------------------------------------------- #
def _block(text: str, header: str) -> str:
    """Return the body lines under a '=== HEADER ===' block, up to the next blank line."""
    m = re.search(re.escape(header) + r"\n(.*?)(?:\n\n|\Z)", text, re.S)
    return m.group(1) if m else ""


# solver/DCOP mode encodes priority as a numeric weight; map back to labels.
WEIGHT_TO_LABEL = {1.0: "critical", 0.75: "high", 0.5: "medium", 0.25: "low"}


def _weight_label(w: str) -> str:
    try:
        return WEIGHT_TO_LABEL.get(round(float(w), 2), w)
    except ValueError:
        return w


def _section(text: str, header_regex: str) -> str:
    """Body lines following a header line that matches header_regex, until a blank line."""
    m = re.search(header_regex + r"[^\n]*\n(.*?)(?:\n\s*\n|\Z)", text, re.S)
    return m.group(1) if m else ""


def parse_priorities(user_prompt: str):
    """
    [{task, priority}] global to the run. Handles both formats:
      - framing:  '=== TASKS (PUBLIC) ===' with 'priority=high'
      - solver:   'Tasks (task | priority_w):' with a numeric weight
    """
    out = []
    body = _block(user_prompt, "=== TASKS (PUBLIC) ===")
    for line in body.splitlines():
        m = re.match(r"\s*-\s*(.+?)\s*\|\s*priority=(\S+)", line)
        if m:
            out.append({"task": m.group(1).strip(), "priority": m.group(2).strip()})
    if out:
        return out
    # solver / DCOP fallback
    body = _section(user_prompt, r"Tasks \(task \| priority_w\):")
    for line in body.splitlines():
        m = re.match(r"\s*-\s*(.+?)\s*\|\s*([0-9.]+)\s*$", line)
        if m:
            out.append({"task": m.group(1).strip(), "priority": _weight_label(m.group(2))})
    return out


# map a task token in either notation to a canonical id, so a cost vector parsed
# from a solver prompt ("T1.i") matches the real assign_task id ("ISSUE-0001::implement")
WORK_LETTER = {"implement": "i", "review": "r", "test": "t", "docs": "d", "triage": "g"}


def canon_task(s: str):
    if not s:
        return None
    m = re.match(r"(?:ISSUE[-‑])?0*(\d+)::([A-Za-z]+)", s) or re.match(r"T(\d+)\.([A-Za-z]+)", s)
    if not m:
        return None
    work = m.group(2)
    letter = work.lower() if len(work) == 1 else WORK_LETTER.get(work.lower(), work.lower())
    return f"{int(m.group(1))}.{letter}"


def parse_persona(user_prompt: str) -> str:
    """
    The 'who you are' framing at the top of an agent's prompt (team player /
    egoist / etc). Falls back to the intro line for solver/DCOP prompts.
    """
    m = re.search(r"=== WHO YOU ARE ===\n(.*?)(?:\n\n===|\Z)", user_prompt, re.S)
    if m:
        return m.group(1).strip()
    # solver / fallback: first text block after the first '=== ... ===' header
    m = re.search(r"===[^\n]*===\n(.*?)(?:\n\n|\Z)", user_prompt, re.S)
    return m.group(1).strip() if m else ""


def parse_costs(user_prompt: str):
    """
    {task: cost_str} per agent. Handles both formats:
      - framing:  '=== YOUR COSTS (PRIVATE) ===' with 'task: cost=X'
      - solver:   'Your cost vector ...:' with 'task | cost=X'
    """
    out = {}
    body = _block(user_prompt, "=== YOUR COSTS (PRIVATE) ===")
    for line in body.splitlines():
        m = re.match(r"\s*-\s*(.+?):\s*cost=(\S+)", line)
        if m:
            out[m.group(1).strip()] = m.group(2).strip()
    if out:
        return out
    # solver / DCOP fallback
    body = _section(user_prompt, r"Your cost vector")
    for line in body.splitlines():
        m = re.match(r"\s*-\s*(.+?)\s*\|\s*cost=(\S+)", line)
        if m:
            out[m.group(1).strip()] = m.group(2).strip()
    return out


def _round_num(key: str) -> int:
    m = re.search(r"(\d+)$", key or "")
    return int(m.group(1)) if m else 0


def build_reasoning(run_dir: Path):
    """
    (agent, phase) -> [reasoning trace per round, in turn order].

    agent_trajectories.json nests agent -> iteration -> phase -> round ->
    trajectory -> step -> {reasoning, tools}. We join the non-empty reasoning
    text of every step in a round = the agent's thinking for that turn.
    """
    p = run_dir / "agent_trajectories.json"
    if not p.exists():
        return {}
    try:
        tj = json.loads(p.read_text())
    except Exception:
        return {}
    out = {}
    for agent, iters in (tj or {}).items():
        for itk in sorted(iters.keys(), key=_round_num):
            for phase, rounds in (iters[itk] or {}).items():
                for rk in sorted(rounds.keys(), key=_round_num):
                    traj = (rounds[rk] or {}).get("trajectory", {}) or {}
                    parts = []
                    for sk in sorted(traj.keys(), key=_round_num):
                        r = (traj[sk].get("reasoning") or "").strip()
                        if r:
                            parts.append(r)
                    out.setdefault((agent, phase), []).append(" ".join(parts))
    return out


def find_run_dir(model: str, framing: str, rtype: str, env: str, seed: int, sampling: int, ts: str = ""):
    """Resolve a run dir from the merged index. The matching run carries its own
    set_dir/ts/model_label provenance; if several timestamps match (same framing +
    dims run more than once), the most recent is used. Pass ts to pin one."""
    md = scan_index()["models"].get(model)
    if not md:
        return None
    matches = [r for r in md["runs"]
               if r["framing"] == framing and r["type"] == rtype
               and (r["env"] or "") == (env or "")
               and r["seed"] == seed and r["sampling"] == sampling
               and (not ts or r["ts"] == ts)]
    if not matches:
        return None
    r = max(matches, key=lambda r: r["ts"])
    base = OUTPUTS / r["set_dir"] / r["ts"] / "runs" / r["model_label"] / framing / rtype
    if env:
        base = base / env
    pattern = str(base / f"*__seed{seed}__s{sampling}")
    hits = [p for p in glob.glob(pattern)
            if Path(p).is_dir() and RUNDIR_RE.search(Path(p).name)]
    return Path(hits[0]) if hits else None


def _run_dir_from_record(r: dict, framing: str, rtype: str, env: str):
    """Build a run dir straight from an index record (avoids re-scanning per run)."""
    base = OUTPUTS / r["set_dir"] / r["ts"] / "runs" / r["model_label"] / framing / rtype
    if env:
        base = base / env
    hits = [p for p in glob.glob(str(base / f"*__seed{r['seed']}__s{r['sampling']}"))
            if Path(p).is_dir() and RUNDIR_RE.search(Path(p).name)]
    return Path(hits[0]) if hits else None


def task_letter(task: str) -> str:
    """'ISSUE-0001::implement' -> 'i'; '1.r' -> 'r'; skip/None -> '-'."""
    if not task or task == "skip":
        return "-"
    m = re.search(r"::([A-Za-z]+)$", task) or re.search(r"\.([A-Za-z])$", task)
    if not m:
        return "?"
    w = m.group(1)
    return w.lower() if len(w) == 1 else WORK_LETTER.get(w.lower(), w[0].lower())


def _agent_costs(run_dir: Path) -> dict:
    """{agent: {canon_task: cost_float}} parsed from agent_prompts.json."""
    out = {}
    ap = run_dir / "agent_prompts.json"
    if not ap.exists():
        return out
    seen = set()
    for e in json.loads(ap.read_text()):
        a = e.get("agent_name")
        if not a or a in seen:
            continue
        seen.add(a)
        cv = {}
        for t, c in parse_costs(e.get("user_prompt", "")).items():
            ct = canon_task(t)
            try:
                cv[ct or t] = float(c)
            except (TypeError, ValueError):
                pass
        out[a] = cv
    return out


def compute_summary(model: str, framing: str, rtype: str, env: str, ts: str = ""):
    """Aggregate, per agent turn-position, across every seed/sample of this table:
       average realized cost and the distribution of realized task types (i/r/t/-),
       plus the group's average realized joint reward. Agents are keyed by turn
       position because their names are randomized per seed; position 0 is the
       designated agent."""
    md = scan_index()["models"].get(model)
    if not md:
        return None
    recs = [r for r in md["runs"]
            if r["framing"] == framing and r["type"] == rtype
            and (r["env"] or "") == (env or "") and (not ts or r["ts"] == ts)]
    if not recs:
        return None

    slots = {}  # pos -> {"cost_sum","n","dist": {letter: count}, "designated": bool}
    joint_sum, joint_n = 0.0, 0
    for r in recs:
        rd = _run_dir_from_record(r, framing, rtype, env)
        if not rd:
            continue
        try:
            mt = json.loads((rd / "metrics.json").read_text())
        except Exception:
            continue
        assign = mt.get("assignment_realized") or {}
        rewards = mt.get("rewards_realized") or {}
        rc_p = rd / "run_config.json"
        order = (json.loads(rc_p.read_text()).get("agent_turn_order")
                 if rc_p.exists() else None) or list(assign.keys())
        costs = _agent_costs(rd)
        jr = mt.get("joint_reward_realized")
        if jr is not None:
            joint_sum += jr
            joint_n += 1
        for i, a in enumerate(order):
            task = assign.get(a)
            letter = task_letter(task)
            cv = costs.get(a, {})
            cost = 0.0 if letter == "-" else cv.get(canon_task(task) or task)
            reward = rewards.get(a)
            s = slots.setdefault(i, {"cost_sum": 0.0, "n": 0, "dist": {},
                                     "reward_sum": 0.0, "reward_n": 0})
            s["dist"][letter] = s["dist"].get(letter, 0) + 1
            if cost is not None:
                s["cost_sum"] += cost
                s["n"] += 1
            if reward is not None:
                s["reward_sum"] += reward
                s["reward_n"] += 1

    agents = [{
        "pos": i,
        "avg_cost": (slots[i]["cost_sum"] / slots[i]["n"]) if slots[i]["n"] else None,
        "avg_reward": (slots[i]["reward_sum"] / slots[i]["reward_n"]) if slots[i]["reward_n"] else None,
        "dist": slots[i]["dist"],
        "n": slots[i]["n"],
    } for i in sorted(slots)]
    return {
        "agents": agents,
        "avg_joint": (joint_sum / joint_n) if joint_n else None,
        "n_runs": joint_n,
    }


def load_chat(model: str, framing: str, rtype: str, env: str, seed: int, sampling: int, ts: str = ""):
    run_dir = find_run_dir(model, framing, rtype, env, seed, sampling, ts)
    if not run_dir:
        return {"ok": False, "framing": framing, "error": "run not found"}
    # provenance (which variant/timestamp this column actually came from)
    rel = run_dir.relative_to(OUTPUTS).parts
    prov_set_dir, prov_ts = rel[0], rel[1]
    prov_variant = model_group(prov_set_dir)[1]

    bb_path = run_dir / "blackboards.json"
    ap_path = run_dir / "agent_prompts.json"
    if not bb_path.exists():
        return {"ok": False, "framing": framing, "error": "blackboards.json missing"}

    blackboards = json.loads(bb_path.read_text())
    bb = blackboards[0] if blackboards else {"participants": [], "events": []}
    participants = bb.get("participants", [])

    # turn order (for alignment) + priorities/costs from prompts
    turn_order = participants[:]
    priorities, costs_by_agent, persona_by_agent = [], {}, {}
    rc_path = run_dir / "run_config.json"
    if rc_path.exists():
        rc = json.loads(rc_path.read_text())
        turn_order = rc.get("agent_turn_order") or turn_order

    if ap_path.exists():
        prompts = json.loads(ap_path.read_text())
        first_seen = {}
        for e in prompts:
            a = e.get("agent_name")
            if a and a not in first_seen:
                first_seen[a] = e.get("user_prompt", "")
        for a, up in first_seen.items():
            costs_by_agent[a] = parse_costs(up)
            persona_by_agent[a] = parse_persona(up)
            if not priorities:
                priorities = parse_priorities(up)

    # realized individual reward per agent + canonical cost lookup (for the
    # assign_task bubble: cost of the chosen task and reward earned for it)
    rewards_by_agent = {}
    joint_reward = None       # run-global team total (sum of individual realized rewards)
    joint_reward_optimal = None  # best achievable team total for this table (type+env)
    mt_path = run_dir / "metrics.json"
    if mt_path.exists():
        mt = json.loads(mt_path.read_text())
        rewards_by_agent = mt.get("rewards_realized") or {}
        joint_reward = mt.get("joint_reward_realized")
        joint_reward_optimal = mt.get("joint_reward_optimal")
    canon_costs = {
        a: {canon_task(t): c for t, c in (costs or {}).items() if canon_task(t)}
        for a, costs in costs_by_agent.items()
    }

    def task_cost(agent_name, tid):
        if not tid or tid == "skip":
            return 0.0
        c = canon_costs.get(agent_name, {}).get(canon_task(tid))
        try:
            return float(c) if c is not None else None
        except (TypeError, ValueError):
            return None

    reasoning_idx = build_reasoning(run_dir)  # (agent, phase) -> [trace per round]

    turn_pos = {a: i for i, a in enumerate(turn_order)}
    PHASE_RANK = {"planning": 0, "execution": 1}

    # walk events, assigning each an alignment key (phase, round, turn position)
    seen_in_phase = {}  # (phase, agent) -> count so far  => round index
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
            extra = {
                "task_id": task_id,
                "cost": task_cost(agent, task_id),
                "reward": rewards_by_agent.get(agent),
            }
        else:
            text = json.dumps(payload)[:400]
            mkind = "other"

        rlist = reasoning_idx.get((agent, phase), [])
        reasoning = rlist[rnd] if rnd < len(rlist) else ""

        messages.append({
            "agent": agent,
            "kind": mkind,
            "text": text,
            "phase": phase,
            "phase_rank": PHASE_RANK.get(phase, 9),
            "round": rnd,
            "pos": turn_pos.get(agent, 99),
            "reasoning": reasoning,
            **extra,
        })

    return {
        "ok": True,
        "framing": framing,
        "participants": participants,
        "turn_order": turn_order,
        "context": context_msg,
        "priorities": priorities,
        "costs_by_agent": costs_by_agent,
        "persona_by_agent": persona_by_agent,
        "joint_reward": joint_reward,
        "joint_reward_optimal": joint_reward_optimal,
        "variant": prov_variant,
        "ts": prov_ts,
        "summary": compute_summary(model, framing, rtype, env, ts),
        "messages": messages,
        "run_dir": str(run_dir),
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


@app.route("/api/chat")
def api_chat():
    a = request.args
    try:
        seed = int(a["seed"])
        sampling = int(a["sampling"])
    except (KeyError, ValueError):
        return jsonify({"ok": False, "error": "seed/sampling required"}), 400
    return jsonify(load_chat(
        a.get("model", ""), a.get("framing", ""), a.get("type", ""), a.get("env", ""),
        seed, sampling, a.get("timestamp", ""),
    ))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    print(f"Self-sacrifice viewer → http://{args.host}:{args.port}  (Ctrl-C to stop)")
    print(f"Scanning outputs at: {OUTPUTS}")
    app.run(host=args.host, port=args.port, debug=False)
