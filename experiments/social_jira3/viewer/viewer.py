#!/usr/bin/env python3
"""
social-jira3 scenario + transcript viewer.

A tiny local Flask app to inspect a run's ground truth and conversation together — the
social_jira2 viewer plus the v3 information surfaces:

  * the per-employee **inbox** (last-day conversation threads) with the signal message(s)
    highlighted, counterpart relations shown (judge-only info — the agents never saw the
    relations or the signal markers);
  * the private **calendar decoy** as a slot grid (core = the equal pairwise overlap);
  * the resolved v3 **axes** (channel / strength / pointer / audience / decoys);
  * each assistant's **closing summary** (private post-execution turn) as its own section
    per column, with its CoT and — when ``judge.py --summaries`` has been run — the
    summary-judge labels (leakage / faithfulness / recap accuracy).

Usage:
    python viewer.py            # then open http://localhost:5002
    python viewer.py --port 8000

Pick model / timestamp / cell (channel-strength-pointer-audience-decoys) / scenario_type /
seed / sample, then tick the personalities to show as columns. Toggle "deliberation" for
CoT, "show private votes" for the evolving ballot table, "show judge phenomena" for the
turn-judge overlay (``judge_results.json``, jira3 taxonomy incl. Signal Uptake/Dismissal).
"""

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path

# Run under whatever interpreter has Flask (re-exec into the repo .venv if needed).
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
OUTPUT_PREFIX = "social_jira3_"

# Run dir names end with '__n<N>__t<T>__seed<seed>__s<sampling>'.
RUNDIR_RE = re.compile(r"__n(?P<n>\d+)__t(?P<t>\d+)__seed(?P<seed>\d+)__s(?P<sampling>\d+)$")

# A cell string is `{channel}-{strength}-conf{X}-{audience}-dec{Y}` (v3), or the legacy
# `{channel}-{strength}-ptr{on|off}-{audience}-dec{Y}` (v1/v2). We split OUT the
# confidentiality token so runs differing ONLY in confidentiality share a `base_cell`
# (with '*' at that position) and can be shown as side-by-side columns for one scenario.
_CONF_ORDER = {"none": 0, "audience": 1, "duty": 2, "self": 3, "generic": 4,
               "off": 0, "on": 4}  # legacy ptr on/off map onto the ends


def parse_cell(cell: str):
    """(confidentiality, base_cell). base_cell has '*' where the confidentiality token was,
    so all confidentiality variants of one scenario collapse to the same base_cell."""
    parts = cell.split("-")
    for i, p in enumerate(parts):
        if p.startswith("conf"):
            conf = p[len("conf"):]
            break
        if p.startswith("ptr"):
            conf = p[len("ptr"):]  # legacy: on/off
            break
    else:
        return None, cell
    base = "-".join(parts[:i] + ["*"] + parts[i + 1:])
    return conf, base


app = Flask(__name__)


# --------------------------------------------------------------------------- #
# Index: outputs/<set>/<ts>/runs/<model>/<setup>/<cell>/<type>/<pers>/<rundir>  #
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
        # <set>/<timestamp>/runs/... (normal sweeps) or <set>/runs/... (resume/merged).
        ts_candidates = []
        if (set_dir / "runs").is_dir():
            ts_candidates.append(("(merged)", set_dir / "runs"))
        for ts_dir in sorted(set_dir.iterdir()):
            if ts_dir.is_dir() and (ts_dir / "runs").is_dir():
                ts_candidates.append((ts_dir.name, ts_dir / "runs"))
        for ts_name, runs_root in ts_candidates:
            for label_root in sorted(d for d in runs_root.iterdir() if d.is_dir()):
                model_label = label_root.name
                # jira3 layout: <setup>/<cell>/<scenario_type>/<personality>/<rundir>
                # (cell = channel-strength-ptr<on|off>-audience[-dec<on|off>]).
                candidates = glob.glob(str(label_root / "*" / "*" / "*" / "*" / "*"))
                for rundir in candidates:
                    rp = Path(rundir)
                    m = RUNDIR_RE.search(rp.name)
                    if not m or not rp.is_dir():
                        continue
                    anc = rp.relative_to(label_root).parts[:-1]
                    if len(anc) != 4:
                        continue
                    setup, cell, rtype, personality = anc
                    conf, base_cell = parse_cell(cell)
                    models.setdefault(display, {"runs": []})["runs"].append({
                        "set_dir": set_dir.name,
                        "ts": ts_name,
                        "model_label": model_label,
                        "setup": setup,
                        "cell": cell,
                        "base_cell": base_cell,
                        "confidentiality": conf,
                        "type": rtype,
                        "personality": personality,
                        "seed": int(m.group("seed")),
                        "sampling": int(m.group("sampling")),
                        "run_dir": str(rp),
                    })
    return {"models": models}


def find_run(runs, ts, setup, base_cell, confidentiality, rtype, seed, sampling):
    """The indexed run matching one confidentiality variant of a base scenario (personality
    is fixed by the base group — these sweeps are all personality=none)."""
    for r in runs:
        if (r["ts"] == ts and r.get("setup", "base") == setup
                and r.get("base_cell") == base_cell
                and r.get("confidentiality") == confidentiality
                and r["type"] == rtype
                and r["seed"] == int(seed) and r["sampling"] == int(sampling)):
            if Path(r["run_dir"]).is_dir():
                return r
    return None


# --------------------------------------------------------------------------- #
# Reasoning / deliberation (unchanged from jira2)                              #
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
# Load one run                                                                 #
# --------------------------------------------------------------------------- #
def load_scenario(run_dir: Path):
    p = run_dir / "scenario.json"
    if not p.exists():
        return None
    sc = json.loads(p.read_text())
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
    """Full system + user prompt for one agent's first turn (verbatim)."""
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


def load_prompt_index(run_dir: Path):
    """(agent, phase, round) -> {system_prompt, user_prompt} for EVERY captured turn.

    Planning rounds are 1-based in ``agent_prompts.json``; execution / survey / summary
    turns have ``round=None``. Lets the viewer show the exact system+user prompt the LLM
    saw for each individual message, not just agent[0]'s first turn."""
    p = run_dir / "agent_prompts.json"
    if not p.exists():
        return {}
    try:
        recs = json.loads(p.read_text())
    except Exception:
        return {}
    idx = {}
    for r in recs or []:
        key = (r.get("agent_name"), r.get("phase"), r.get("round"))
        idx[key] = {"system_prompt": r.get("system_prompt", ""),
                    "user_prompt": r.get("user_prompt", "")}
    return idx


def _norm_pair(s):
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
    p = run_dir / "agent_votes.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def attach_vote_states(messages, votes, scenario):
    """Attach an evolving private-ballot snapshot to each message (see jira2 viewer)."""
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


# Level-2 confirmation files (written by judge.py --level2) folded into the turn overlay as
# their own phenomena. Maps the per-run L2 output filename -> the display phenomenon name.
L2_PHENOMENA = {
    "judge_l2_fabrication_executed.json": "L2 Fabrication (executed)",
}


def _vround_of(phase: str, jround) -> int:
    """Map a judged turn's (phase, round) to the vote round used as its overlay key."""
    if phase == "planning" and isinstance(jround, int) and jround > 0:
        return jround - 1
    return 0


def load_judge(run_dir: Path):
    """Turn-judge results (``judge_results.json``); see the jira2 viewer for conventions.
    Level-2 confirmation passes (``judge_l2_*.json``) are folded in as independent
    phenomena (e.g. ``L2 Fabrication (executed)``), one incident per confirmed turn."""
    p = run_dir / "judge_results.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
    except Exception:
        return None

    incidents: list = []
    counts: dict = {}
    turn_status: dict = {}  # "agent|phase|vround" -> "judged" | "failed" (parse_error)
    for t in d.get("turns") or []:
        agent = t.get("agent")
        phase = t.get("phase") or "planning"
        vround = _vround_of(phase, t.get("round"))
        turn_status[f"{agent}|{phase}|{vround}"] = "failed" if t.get("parse_error") else "judged"
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

    # Fold in level-2 confirmation passes as independent phenomena. Each L2 file confirms
    # one L1 phenomenon; a confirmed turn (present==True) becomes one "L2 <phenomenon>"
    # incident, kept separate from the L1 phenomenon so the two are counted independently.
    for l2_name, l2_phenomenon in L2_PHENOMENA.items():
        l2p = run_dir / l2_name
        if not l2p.exists():
            continue
        try:
            l2d = json.loads(l2p.read_text())
        except Exception:
            continue
        for t in l2d.get("turns") or []:
            if not t.get("present"):
                continue
            phase = t.get("phase") or "planning"
            counts[l2_phenomenon] = counts.get(l2_phenomenon, 0) + 1
            incidents.append({
                "agent": t.get("agent"), "phase": phase,
                "vround": _vround_of(phase, t.get("round")),
                "turn_index": t.get("turn_index"),
                "phenomenon": l2_phenomenon,
                "spans": t.get("spans") or [],
                "note": t.get("note") or "",
            })

    return {
        "schema": "phenomena",
        "judge_model": d.get("judge_model"),
        "num_turns": d.get("num_turns") or len(d.get("turns") or []),
        "total_flags": len(incidents),
        "counts": counts,
        "n_errors": n_errors,
        "incidents": incidents,
        "turn_status": turn_status,
    }


def load_summaries(run_dir: Path):
    """Closing summaries (``summaries.json``): [{agent, audience, text, reasoning}] in
    turn order, plus the summary-judge labels (``judge_summary_results.json``) if present."""
    p = run_dir / "summaries.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
    except Exception:
        return None
    order = None
    rc = run_dir / "run_config.json"
    if rc.exists():
        try:
            order = json.loads(rc.read_text()).get("agent_turn_order")
        except Exception:
            order = None
    agents = [a for a in (order or sorted(data))] if data else []

    judge_by_agent = {}
    jp = run_dir / "judge_summary_results.json"
    if jp.exists():
        try:
            jd = json.loads(jp.read_text())
            for s in jd.get("summaries") or []:
                judge_by_agent[s.get("agent")] = {
                    k: s.get(k) for k in
                    ("leakage", "faithfulness", "recap_accuracy", "mentions_pairing", "parse_error")
                }
        except Exception:
            pass

    prompt_idx = load_prompt_index(run_dir)
    out = []
    for a in agents:
        rec = data.get(a) or {}
        out.append({
            "agent": a,
            "audience": rec.get("audience"),
            "text": (rec.get("text") or "").strip(),
            "reasoning": "\n\n".join(x for x in (rec.get("reasoning") or []) if x).strip(),
            "judge": judge_by_agent.get(a),
            "prompt": prompt_idx.get((a, "summary", None)),
        })
    return out


def load_outcome(run_dir: Path):
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
    prompt_idx = load_prompt_index(run_dir)

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

        # Prompt records: planning rounds are 1-based; execution has round=None. Fall
        # back to the round-less record so both the announce + the commit in one
        # execution turn point at that turn's single prompt.
        prompt = (prompt_idx.get((agent, phase, rnd + 1))
                  or prompt_idx.get((agent, phase, None)))

        messages.append({
            "agent": agent, "kind": mkind, "text": text,
            "phase": phase, "round": rnd,
            "deliberation": deliberation, "prompt": prompt, **extra,
        })

    return {"participants": participants, "turn_order": turn_order,
            "context": context_msg, "messages": messages,
            "reasoning_source": reasoning_source}


def load_cell(model, ts, setup, base_cell, rtype, seed, sampling, confidentialities):
    """Scenario panel (shared — same seed => identical instance across confidentiality) + one
    conversation column per requested confidentiality variant. Confidentiality only changes
    the system prompt, so the four columns share roster/tasks/goodness/feelings/inbox but
    differ in the norm each agent saw (visible in the per-column prompt block) and in what
    they did with it."""
    md = scan_index()["models"].get(model) or {}
    runs = md.get("runs", [])
    scenario = None
    columns = []
    for conf in confidentialities:
        r = find_run(runs, ts, setup, base_cell, conf, rtype, seed, sampling)
        if not r:
            columns.append({"confidentiality": conf, "label": conf, "ok": False,
                            "error": "run not found"})
            continue
        rd = Path(r["run_dir"])
        if scenario is None:
            scenario = load_scenario(rd)
        chat = load_messages(rd)
        outcome = load_outcome(rd)
        vote_meta = attach_vote_states(chat["messages"], load_votes(rd), scenario)
        prompt0 = load_agent_prompt(rd, (chat.get("turn_order") or [None])[0])
        columns.append({"confidentiality": conf, "label": conf,
                        "personality": r.get("personality"), "ok": True, "run_dir": str(rd),
                        "prompt0": prompt0, "votes": vote_meta,
                        "judge": load_judge(rd),
                        "summaries": load_summaries(rd),
                        **chat, **outcome})
    return {"scenario": scenario, "columns": columns,
            "dims": {"model": model, "ts": ts, "setup": setup, "base_cell": base_cell,
                     "type": rtype, "seed": seed, "sampling": sampling}}


# --------------------------------------------------------------------------- #
# Judge phenomenon taxonomy — derived from the judge prompt so it can't drift  #
# --------------------------------------------------------------------------- #
JUDGE_PROMPT_PATH = (HERE / ".." / "JUDGE_TURN_PROMPT.md").resolve()
# Fallback if the prompt can't be read/parsed — kept in sync with the prompt's
# "## Phenomenon Taxonomy" section (order matters; it's the display order).
_PHENOMENA_FALLBACK = [
    "Signal Uptake", "Signal Dismissal", "Observer Suspicion Modeling",
    "Strategic Omission", "Strategic Framing", "Fabrication (considered)",
    "Fabrication (executed)", "L2 Fabrication (executed)", "Hallucination",
    "Leakage", "Implied Leakage",
    "Zero-Pressure Leakage", "Made Bad Deal", "Out-of-Scope Proposal",
    "Suggest Mediation", "Vague", "Ambiguous",
]


def _with_l2_phenomena(names: list) -> list:
    """Insert each level-2 phenomenon right after its L1 sibling (or append), so L2 passes
    show up in the taxonomy even though they aren't headers in the judge prompt."""
    out = list(names)
    for l2_phenomenon in L2_PHENOMENA.values():
        if l2_phenomenon in out:
            continue
        base = l2_phenomenon[3:] if l2_phenomenon.startswith("L2 ") else None
        if base and base in out:
            out.insert(out.index(base) + 1, l2_phenomenon)
        else:
            out.append(l2_phenomenon)
    return out


def load_phenomena_taxonomy() -> list:
    """Parse the phenomenon names out of the judge prompt's ``## Phenomenon Taxonomy``
    section (each phenomenon is a bold header ``**Name**`` on its own line), so the viewer
    always tracks the latest taxonomy. Falls back to a baked-in copy on any error."""
    try:
        lines = JUDGE_PROMPT_PATH.read_text(encoding="utf-8").splitlines()
        names, in_section = [], False
        for ln in lines:
            s = ln.strip()
            if s.startswith("## "):
                in_section = s == "## Phenomenon Taxonomy"
                continue
            if in_section:
                m = re.match(r"^\*\*([^*{}]+)\*\*$", s)  # exclude template headers like **{agent}...**
                if m:
                    names.append(m.group(1).strip())
        return _with_l2_phenomena(names) if names else _PHENOMENA_FALLBACK
    except Exception:
        return _PHENOMENA_FALLBACK


# --------------------------------------------------------------------------- #
# Routes                                                                       #
# --------------------------------------------------------------------------- #
@app.route("/")
def home():
    return send_from_directory(HERE, "index.html")


@app.route("/api/index")
def api_index():
    return jsonify({**scan_index(), "phenomena_taxonomy": load_phenomena_taxonomy()})


@app.route("/api/cell")
def api_cell():
    a = request.args
    try:
        seed = int(a["seed"])
        sampling = int(a["sampling"])
    except (KeyError, ValueError):
        return jsonify({"error": "seed/sampling required"}), 400
    confidentialities = [c for c in (a.get("confidentiality", "").split(",")) if c]
    return jsonify(load_cell(a.get("model", ""), a.get("timestamp", ""),
                             a.get("setup", "base"),
                             a.get("base_cell", ""), a.get("type", ""),
                             seed, sampling, confidentialities))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5002)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    print(f"social-jira3 viewer → http://{args.host}:{args.port}  (Ctrl-C to stop)")
    print(f"Scanning outputs at: {OUTPUTS}")
    app.run(host=args.host, port=args.port, debug=False)
