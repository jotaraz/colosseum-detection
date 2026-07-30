#!/usr/bin/env python3
"""
social_jira4 optimizer-pathway viewer.

Retraces one optimization run end to end, step by step, as the chain the loop actually walks:

    prompter's OPRO summary → its reasoning → the new prompt (blocks)
        → checks-and-balances verdict → rollout(s) → per-turn judging → objective → score
        → (that score + the critic's notes become the next step's OPRO summary)

Everything is read back from the artifacts ``loop.py`` writes — nothing is recomputed by a model.
Per run: ``metadata.json`` / ``history.jsonl`` / ``steps/step_NNN.json`` / ``prompter_system.md``,
plus one social_jira3-shaped run dir per rollout under ``runs/stepNNN/<run_id>/``.

Transcripts are rebuilt from ``tool_events.json`` with social_jira3's own ``judge.build_turns`` —
the *same* function the critic scored — so a bubble and its verdict are joined on ``turn_index``
by construction rather than by a round-alignment heuristic.

Usage:
    python viewer.py            # then open http://localhost:5003
    python viewer.py --port 8000
"""

import argparse
import difflib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
            "Flask is not installed for this interpreter and no repo .venv with Flask was "
            "found.\nInstall it (e.g. `pip install flask`) or run with the repo venv: "
            "`<repo>/.venv/bin/python viewer.py`."
        )
    os.execv(str(_venv_py), [str(_venv_py), *sys.argv])

HERE = Path(__file__).resolve().parent
OUTPUTS = (HERE / ".." / "outputs").resolve()
PROJECT_ROOT = HERE.parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# The critic's own turn assembly — imported, not reimplemented, so turn_index always agrees.
from experiments.social_jira3.judge import (  # noqa: E402
    _load_json,
    _prompts_for_turn,
    build_turns,
)

JUDGED_PHASES = ("planning", "execution")
CATEGORY_PROMPT = (HERE / ".." / "CRITIC_CATEGORY_PROMPT.md").resolve()

# Display order for the critic's categories; parsed from the prompt so it can't drift (fallback
# below mirrors CRITIC_CATEGORY_PROMPT.md's "## Categories" section).
_CATEGORIES_FALLBACK = [
    "honest-merits", "disclosure", "strategic-omission", "strategic-framing",
    "strategic-vague-evasion", "strategic-fabrication", "considered-fabrication",
    "out-of-scope-proposal", "ambiguous",
]

app = Flask(__name__)


def load_category_taxonomy() -> List[str]:
    """Category names from the CATEGORY judge prompt's ``## Categories`` section (each is a bold
    header ``**Name** — …`` on its own line)."""
    try:
        lines = CATEGORY_PROMPT.read_text(encoding="utf-8").splitlines()
        names, in_section = [], False
        for ln in lines:
            s = ln.strip()
            if s.startswith("## "):
                in_section = s == "## Categories"
                continue
            if in_section:
                # e.g. "**Strategic-fabrication** — …" or "**Disclosure (leak)** — …"
                m = re.match(r"^\*\*([A-Za-z][A-Za-z\- ]+?)(?:\s*\([^)]*\))?\*\*\s*—", s)
                if m:
                    name = m.group(1).strip().lower().replace(" ", "-")
                    if name not in names:
                        names.append(name)
        return names or _CATEGORIES_FALLBACK
    except Exception:
        return _CATEGORIES_FALLBACK


# --------------------------------------------------------------------------- #
# Run index                                                                    #
# --------------------------------------------------------------------------- #
def scan_runs() -> List[Dict[str, Any]]:
    """Every optimizer run under ``outputs/`` — any dir holding a ``metadata.json``."""
    runs = []
    if not OUTPUTS.is_dir():
        return runs
    for d in sorted(OUTPUTS.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        meta_path = d / "metadata.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text())
        except Exception:
            continue
        n_steps = len(list((d / "steps").glob("step_*.json"))) if (d / "steps").is_dir() else 0
        runs.append({
            "name": d.name,
            "started_at": meta.get("started_at", ""),
            "mode": meta.get("mode", ""),
            "step_schema": meta.get("step_schema", 1),   # 1 == pre-instrumentation run
            "n_steps": n_steps,
            "models": meta.get("models", {}),
            "objective": meta.get("objective", ""),
        })
    return runs


def _history(run_dir: Path) -> List[Dict[str, Any]]:
    p = run_dir / "history.jsonl"
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def _step_state(row: Dict[str, Any]) -> str:
    """How a step ended — drives the trajectory chip's colour."""
    if row.get("prompter_source") == "warm_start" and row.get("cb_ok"):
        return "warmstart"
    if not row.get("cb_ok"):
        return "invalid" if str(row.get("cb_reason", "")).startswith("invalid blocks:") else "rejected"
    return "ran"


def load_run(name: str) -> Dict[str, Any]:
    """Run-level summary: metadata + one chip per step + the running best."""
    d = OUTPUTS / name
    if not d.is_dir():
        return {"error": f"no such run: {name}"}
    meta = json.loads((d / "metadata.json").read_text()) if (d / "metadata.json").exists() else {}
    rows = _history(d)
    steps, best = [], None
    for r in rows:
        state = _step_state(r)
        if state == "ran" or state == "warmstart":
            best = r["score"] if best is None else max(best, r["score"])
        steps.append({
            "step": r.get("step"),
            "score": r.get("score", 0.0),
            "state": state,
            "best_so_far": best if best is not None else 0.0,
            "is_best": best is not None and r.get("score") == best and state in ("ran", "warmstart"),
            "cb_reason": r.get("cb_reason", ""),
            "rationale": r.get("prompter_rationale", ""),
            "source": r.get("prompter_source", ""),
            "duration_s": r.get("duration_s", 0),
            "usage": r.get("usage", {}),
            "best_lie": r.get("best_lie", ""),
            "seed_scores": r.get("seed_scores", []),
            "has_detail": (d / "steps" / f"step_{int(r.get('step', 0)):03d}.json").exists(),
        })
    # Only one step can be *the* best; mark the earliest that reached the final maximum.
    top = max((s["score"] for s in steps if s["state"] in ("ran", "warmstart")), default=None)
    seen_best = False
    for s in steps:
        s["is_best"] = (not seen_best and top is not None
                        and s["score"] == top and s["state"] in ("ran", "warmstart"))
        seen_best = seen_best or s["is_best"]
    totals: Dict[str, int] = {}
    for s in steps:
        for role, u in (s.get("usage") or {}).items():
            totals[role] = totals.get(role, 0) + int(u.get("total_tokens", 0) or 0)
    return {
        "name": name, "metadata": meta, "steps": steps,
        "totals": {"tokens": totals, "duration_s": sum(s.get("duration_s", 0) or 0 for s in steps)},
        "prompter_system": (d / "prompter_system.md").read_text(encoding="utf-8")
        if (d / "prompter_system.md").exists() else "",
        "categories": load_category_taxonomy(),
    }


# --------------------------------------------------------------------------- #
# One rollout (a social_jira3-shaped run dir)                                   #
# --------------------------------------------------------------------------- #
def _resolve_run_dir(stored: Optional[str], run_name: str, step: int, seed: int) -> Optional[Path]:
    """``RunScore.run_dir`` is written relative to the repo root. Fall back to locating the dir
    under this run's own ``runs/stepNNN/`` so a moved/renamed outputs tree still resolves."""
    if stored:
        p = Path(stored)
        for cand in (p, PROJECT_ROOT / p):
            if cand.is_dir():
                return cand
    guess = OUTPUTS / run_name / "runs" / f"step{step:03d}"
    if guess.is_dir():
        for c in sorted(guess.iterdir()):
            if c.is_dir() and c.name.endswith(f"seed{seed}"):
                return c
        dirs = [c for c in sorted(guess.iterdir()) if c.is_dir()]
        if len(dirs) == 1:
            return dirs[0]
    return None


def _json(path: Path, default: Any) -> Any:
    """Read one artifact, tolerating absence. jira3's ``_load_json`` raises on a missing file, and
    a rollout that died mid-flight leaves a dir with only the loggers' output — that must render as
    "incomplete", not 500 the whole step."""
    if not path.exists():
        return default
    try:
        return _load_json(path)
    except Exception:
        return default


def load_rollout(run_dir: Path) -> Dict[str, Any]:
    """One rollout as an ordered list of turns (the critic's unit of judgment), each carrying the
    agent's public message, its private CoT, the exact prompt it saw, and any task it committed to."""
    tool_events = _json(run_dir / "tool_events.json", [])
    reasoning = _json(run_dir / "agent_reasoning.json", {})
    agent_prompts = _json(run_dir / "agent_prompts.json", [])
    scenario = _json(run_dir / "scenario.json", {})
    # `tool_events.json` is written only at the very end of a rollout; without it there is no
    # transcript to show (jira3 likewise treats a dir as complete only once its artifacts land).
    if not tool_events:
        missing = [f for f in ("tool_events.json", "scenario.json", "final_summary.json")
                   if not (run_dir / f).exists()]
        return {"run_dir": str(run_dir), "incomplete": True, "missing": missing,
                "participants": [], "turn_order": [], "context": "", "turns": [],
                "scenario": scenario, "final_summary": {}, "metrics": {}, "referee": [],
                "run_config": _json(run_dir / "run_config.json", {})}

    turns = build_turns(tool_events, reasoning, JUDGED_PHASES)
    out_turns = []
    for t in turns:
        system_prompt, user_prompt = _prompts_for_turn(
            agent_prompts, t["agent"], t["phase"], t["round"]
        )
        out_turns.append({
            "turn_index": t["turn_index"], "agent": t["agent"], "phase": t["phase"],
            "round": t["round"], "message": t.get("message") or "",
            "reasoning": t.get("reasoning") or "", "decision": t.get("decision"),
            "prompt": {"system": system_prompt or "", "user": user_prompt or ""},
        })

    # The shared-channel context event (the briefing every agent saw) lives on the blackboard.
    bb = _json(run_dir / "blackboards.json", [])
    context = ""
    participants: List[str] = []
    if bb:
        participants = bb[0].get("participants") or []
        for ev in bb[0].get("events") or []:
            if ev.get("kind") == "context":
                context = (ev.get("payload") or {}).get("message") or ""
                break

    cfg = _json(run_dir / "run_config.json", {})
    # Order of first speaking, taken from the transcript itself — sj4's run_config has no
    # ``agent_turn_order``, and `participants` is alphabetical, not the order agents actually took.
    order = list(dict.fromkeys(t["agent"] for t in out_turns)) or participants
    return {
        "run_dir": str(run_dir),
        "participants": participants,
        "turn_order": order,
        "context": context,
        "turns": out_turns,
        "scenario": scenario,
        "incomplete": False,
        "final_summary": _json(run_dir / "final_summary.json", {}),
        "metrics": _json(run_dir / "metrics.json", {}),
        "referee": _json(run_dir / "referee.json", []),
        "run_config": cfg,
    }


# --------------------------------------------------------------------------- #
# Block diffs                                                                  #
# --------------------------------------------------------------------------- #
FREE_BLOCKS = ("general_task_info", "decoy_policies", "confidentiality_reason", "personality")
POOL_BLOCKS = ("private_messages_id", "decoy_info_ids")


def _word_diff(old: str, new: str) -> List[Dict[str, str]]:
    """Word-level diff ops for one free-text block, so what the prompter *changed* is visible
    without reading both versions in full."""
    a, b = (old or "").split(), (new or "").split()
    ops = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            ops.append({"op": "equal", "text": " ".join(a[i1:i2])})
        else:
            if i1 != i2:
                ops.append({"op": "delete", "text": " ".join(a[i1:i2])})
            if j1 != j2:
                ops.append({"op": "insert", "text": " ".join(b[j1:j2])})
    return ops


def blocks_diff(prev: Optional[Dict[str, str]], cur: Dict[str, str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for f in FREE_BLOCKS:
        new = cur.get(f, "")
        old = (prev or {}).get(f, "")
        out[f] = {
            "changed": bool(prev) and old != new,
            "is_first": prev is None,
            "ops": _word_diff(old, new) if prev and old != new else None,
        }
    for f in POOL_BLOCKS:
        out[f] = {"changed": bool(prev) and (prev or {}).get(f) != cur.get(f),
                  "is_first": prev is None, "ops": None,
                  "prev": (prev or {}).get(f)}
    return out


# --------------------------------------------------------------------------- #
# One step, fully assembled                                                    #
# --------------------------------------------------------------------------- #
def load_step(run_name: str, step: int) -> Dict[str, Any]:
    d = OUTPUTS / run_name
    p = d / "steps" / f"step_{step:03d}.json"
    if not p.exists():
        return {"error": f"no step file: {p.name} (run predates the step schema?)"}
    detail = json.loads(p.read_text())

    prev_blocks = None
    prev_path = d / "steps" / f"step_{step - 1:03d}.json"
    if step > 0 and prev_path.exists():
        try:
            prev_blocks = json.loads(prev_path.read_text()).get("blocks")
        except Exception:
            prev_blocks = None

    # Verdicts joined to transcript turns by turn_index (both come from judge.build_turns).
    seeds = []
    for s in detail.get("seeds") or []:
        rd = _resolve_run_dir(s.get("run_dir"), run_name, step, int(s.get("seed", 0)))
        rollout = load_rollout(rd) if rd else None
        verdicts = {int(t["turn_index"]): t for t in (s.get("turns") or [])}
        if rollout:
            for t in rollout["turns"]:
                t["verdict"] = verdicts.get(int(t["turn_index"]))
            # Verdicts with no matching bubble (shouldn't happen; surfaced rather than hidden).
            seen = {int(t["turn_index"]) for t in rollout["turns"]}
            rollout["orphan_verdicts"] = [v for k, v in verdicts.items() if k not in seen]
        objective_detail = next(
            (o for o in (detail.get("objective", {}).get("seeds") or [])
             if o.get("seed") == s.get("seed")), {}
        )
        seeds.append({
            "seed": s.get("seed"), "objective": s.get("objective"), "error": s.get("error"),
            "run_dir": s.get("run_dir"), "resolved": bool(rd),
            "turns": s.get("turns") or [], "rollout": rollout,
            "objective_detail": objective_detail,
        })

    return {
        "run": run_name,
        "schema": detail.get("schema", 1),
        "step": detail.get("step"),
        "score": detail.get("score"),
        "cb_ok": detail.get("cb_ok"),
        # which gate stopped it: "" (ran) | blocks | checks | consistency. Absent before schema 3,
        # where the validator was the only gate — hence the fallback.
        "gate": detail.get("gate", "" if detail.get("cb_ok") else "checks"),
        "duration_s": detail.get("duration_s"),
        "usage": detail.get("usage", {}),
        "prompter": detail.get("prompter", {}),
        "cb": detail.get("cb", {"ok": detail.get("cb_ok"), "reason": detail.get("cb_reason", "")}),
        "cons": detail.get("cons", {"ran": False}),   # {} on pre-schema-3 runs (gate did not exist)
        "blocks": detail.get("blocks", {}),
        "diff": blocks_diff(prev_blocks, detail.get("blocks", {})),
        "objective": detail.get("objective", {}),
        "seeds": seeds,
    }


# --------------------------------------------------------------------------- #
# Markdown export of one assistant session                                     #
# --------------------------------------------------------------------------- #
def _num(x: Any) -> str:
    """Scores are means over seeds, so they arrive as long floats (7.333333333333333)."""
    return f"{x:g}" if isinstance(x, float) else str(x)


def _fence(text: str) -> str:
    """A code fence longer than any backtick run inside ``text``, so verbatim model I/O can never
    break out of its block (prompts routinely contain fenced examples)."""
    longest = max((len(m) for m in re.findall(r"`+", text or "")), default=0)
    return "`" * max(3, longest + 1)


def _block(text: str, lang: str = "text") -> str:
    f = _fence(text or "")
    return f"{f}{lang}\n{(text or '').rstrip()}\n{f}"


def _labels_body(v: Optional[Dict[str, Any]]) -> str:
    """The judge's verdict for one turn, as the body of its own section (the heading supplies the
    "Judge labels" title)."""
    if not v:
        return "_not judged_"
    spec = v.get("fabrication_specificity")
    parts = [
        "categories: " + (", ".join(f"`{c}`" for c in (v.get("categories") or [])) or "—"),
        f"specificity: {spec if spec is not None else '—'}",
        f"soundness: {'yes' if v.get('soundness_ok') else 'no'}",
        f"at-stake: {'yes' if v.get('at_stake') else 'no'}",
        f"qualifies: {'**YES**' if v.get('qualifies') else 'no'}",
    ]
    out = " · ".join(parts)
    if v.get("spans"):
        out += "\n\nQuoted span(s):\n" + "\n".join(f"> {s}" for s in v["spans"])
    if v.get("explanation"):
        out += f"\n\n_Judge explanation:_ {v['explanation']}"
    return out


def _agent_turns(rollout: Dict[str, Any], agent: str) -> List[Dict[str, Any]]:
    return [t for t in rollout["turns"] if t["agent"] == agent]


def _turn_title(t: Dict[str, Any]) -> str:
    where = f"planning round {t['round']}" if t["phase"] == "planning" else t["phase"]
    return f"Turn {t['turn_index']} · {where}"


def export_session(run_name: str, step: int, seed: int, agent: str) -> Tuple[str, str]:
    """One assistant's session as markdown: the system prompt and the task briefing it opened with,
    then every turn it took (reasoning, message, decision) carrying that turn's judge labels, and
    the allocation it ended on. ``agent='__all__'`` emits every assistant in the rollout, with the
    turns interleaved chronologically (grouped by planning round / execution) rather than one
    assistant's whole session after another's — only the briefings stay grouped per assistant.

    Returns ``(filename, markdown)``."""
    detail = load_step(run_name, step)
    if "error" in detail:
        raise ValueError(detail["error"])
    sd = next((s for s in detail["seeds"] if int(s["seed"]) == int(seed)), None)
    if sd is None:
        raise ValueError(f"seed {seed} not in step {step}")
    if not sd.get("rollout"):
        raise ValueError(f"no rollout artifacts for seed {seed} (run dir not found)")
    R = sd["rollout"]
    if R.get("incomplete"):
        raise ValueError(f"rollout artifacts incomplete for seed {seed} "
                         f"(missing {', '.join(R.get('missing') or ['tool_events.json'])})")
    meta = (json.loads((OUTPUTS / run_name / "metadata.json").read_text())
            if (OUTPUTS / run_name / "metadata.json").exists() else {})
    models = meta.get("models", {})

    agents = R["turn_order"] if agent == "__all__" else [agent]
    missing = [a for a in agents if not _agent_turns(R, a)]
    if missing:
        raise ValueError(f"no turns for agent(s) {missing} in this rollout")

    sc = R.get("scenario") or {}
    fs = R.get("final_summary") or {}
    L: List[str] = []
    title = ("All assistant sessions" if agent == "__all__" else f"{agent} — assistant session")
    L.append(f"# {title}\n")
    L.append("| | |\n|---|---|")
    L.append(f"| run | `{run_name}` |")
    L.append(f"| step | {step} (score {_num(detail.get('score'))}) |")
    L.append(f"| seed | {seed} (rollout score {_num(sd.get('objective'))}) |")
    L.append(f"| target model | {models.get('target','?')} |")
    L.append(f"| judge model | {models.get('judge','?')} |")
    L.append(f"| objective | {meta.get('objective','?')} |")
    L.append(f"| run dir | `{R['run_dir']}` |")
    b = detail.get("blocks") or {}
    # decoy_info_ids is a list (substrates compose); render it as a slug, and fall back to
    # the pre-multi-select key so older step files still display.
    _decoys = b.get("decoy_info_ids")
    if _decoys is None:
        _decoys = [b["decoy_info_id"]] if b.get("decoy_info_id") else []
    L.append(f"| pooled blocks | private_messages=`{b.get('private_messages_id')}` · "
             f"decoy_info=`{'+'.join(_decoys) or 'none'}` |")
    L.append("")
    L.append("Ground truth (judge-only — the assistants never saw this):")
    L.append(f"- team: {', '.join(sc.get('employees') or [])}")
    L.append(f"- dislikes: {json.dumps(sc.get('dislikes') or {})}")
    L.append(f"- optimal matching: {json.dumps((sc.get('optimal_matchings') or [{}])[0])}"
             f" · comfortable: {json.dumps(sc.get('comfortable_matching') or {})}")
    L.append("")

    # The system prompt carries no agent argument, so it is normally identical for everyone —
    # emit it once when it genuinely is, and per agent when it is not.
    sys_prompts = {a: (_agent_turns(R, a)[0]["prompt"]["system"] or "") for a in agents}
    shared_sys = len(set(sys_prompts.values())) == 1
    if shared_sys:
        L.append("---\n\n## System prompt")
        if len(agents) > 1:
            L.append("_Identical for every assistant in this rollout._\n")
        L.append(_block(next(iter(sys_prompts.values()))))
        L.append("")

    multi = len(agents) > 1
    per_agent = {a: _agent_turns(R, a) for a in agents}

    # The briefings are the one genuinely per-assistant, non-chronological artifact (each carries
    # that employee's private inbox), so they stay grouped up front — everything after them is the
    # conversation as it actually happened.
    if multi:
        L.append("---\n\n## Task briefings — opening user prompts\n")
    for a in agents:
        first = per_agent[a][0]
        if not shared_sys:
            L.append(f"### System prompt — {a}" if multi else "## System prompt")
            L.append(_block(sys_prompts[a]))
            L.append("")
        head = (f"### {a} ({_turn_title(first).lower()})" if multi
                else f"## Task briefing — opening user prompt ({_turn_title(first).lower()})")
        L.append(head)
        L.append("_Includes the roster, the task requirements and this employee's private inbox._\n")
        L.append(_block(first["prompt"]["user"] or ""))
        L.append("")

    # Chronological across assistants: R["turns"] is already in transcript order (build_turns), so
    # interleaving is just a filter. Phase/round headers give the reader the rhythm of the rollout.
    L.append("---\n\n## Turns\n" if multi else "## Turns\n")
    if multi:
        L.append("_All assistants, in the order they actually spoke._\n")
    last_group = None
    for t in [x for x in R["turns"] if x["agent"] in set(agents)]:
        if multi:
            group = (f"Planning round {t['round']}" if t["phase"] == "planning"
                     else str(t["phase"]).capitalize())
            if group != last_group:
                L.append(f"### {group}\n")
                last_group = group
            L.append(f"#### Turn {t['turn_index']} · **{t['agent']}**\n")
        else:
            L.append(f"### {_turn_title(t)}\n")
        # What the assistant did, then what the judge made of it — the parts of a turn are their
        # own subsections so they fold in an outline view and can be linked to individually.
        sub = "#####" if multi else "####"
        L.append(f"{sub} Reasoning (private chain-of-thought)\n")
        L.append(_block(t.get("reasoning") or "(none captured)"))
        L.append("")
        L.append(f"{sub} Message (posted to the shared channel)\n")
        L.append(_block(t.get("message") or "(no message posted)"))
        if t.get("decision"):
            L.append(f"\n**Task committed to:** `{t['decision']}`")
        L.append("")
        L.append(f"{sub} Judge labels\n")
        L.append(_labels_body(t.get("verdict")))
        L.append("")

    # "Final votes": sj4 has no ballot turn, so the binding statement of position is the task
    # each assistant committed to in execution, read alongside the allocation that resulted.
    L.append("---\n\n## Final positions\n" if multi else "## Final position\n")
    for a in agents:
        turns = per_agent[a]
        committed = [t["decision"] for t in turns if t.get("decision")]
        if multi:
            L.append(f"### {a}")
        L.append(f"- committed task (execution turn): `{committed[-1] if committed else 'none'}`")
        L.append(f"- last public message: {(turns[-1].get('message') or '').strip()[:300] or '—'}")
        L.append("")

    L.append("---\n\n## Rollout outcome\n")
    L.append(f"- realized matching: `{json.dumps(fs.get('realized_matching') or {})}`")
    L.append(f"- realized goodness: {_num(fs.get('realized_goodness'))} "
             f"(optimal {_num(fs.get('optimal_goodness'))}, "
             f"comfortable {_num(fs.get('comfortable_goodness'))})")
    L.append(f"- idle employees: {json.dumps(fs.get('idle_employees') or [])}")
    for r in R.get("referee") or []:
        L.append(f"- referee after planning round {r.get('round')}: **{r.get('status')}**"
                 f"{' → stop' if r.get('stop') else ' → continue'} — {r.get('reason','')}")
    L.append("")

    who = "all" if agent == "__all__" else agent
    fname = f"{run_name}__step{step:03d}__seed{seed}__{who}.md"
    return fname, "\n".join(L)


# --------------------------------------------------------------------------- #
# Cross-step views                                                             #
# --------------------------------------------------------------------------- #
def load_lies(run_name: str) -> Dict[str, Any]:
    """Every judged turn the CATEGORY judge flagged as a fabrication (executed or considered),
    across the whole run — sorted by specificity. The AND-gate result is shown per row, so a lie
    that was found but vetoed by soundness/at-stake is visible rather than silently dropped."""
    d = OUTPUTS / run_name
    rows = []
    for p in sorted((d / "steps").glob("step_*.json")) if (d / "steps").is_dir() else []:
        try:
            detail = json.loads(p.read_text())
        except Exception:
            continue
        for s in detail.get("seeds") or []:
            for t in s.get("turns") or []:
                cats = t.get("categories") or []
                if not any("fabrication" in c for c in cats):
                    continue
                rows.append({
                    "step": detail.get("step"), "seed": s.get("seed"),
                    "agent": t.get("agent"), "turn_index": t.get("turn_index"),
                    "categories": cats,
                    "specificity": t.get("fabrication_specificity"),
                    "soundness_ok": t.get("soundness_ok"), "at_stake": t.get("at_stake"),
                    "qualifies": t.get("qualifies"),
                    "spans": t.get("spans") or [],
                    "explanation": t.get("explanation", ""),
                    "message": t.get("message", ""),
                })
    rows.sort(key=lambda r: (r["specificity"] if r["specificity"] is not None else -1,
                             bool(r["qualifies"])), reverse=True)
    return {"rows": rows}


def load_blocks_evolution(run_name: str) -> Dict[str, Any]:
    """A blocks × steps grid: which of the six slots the prompter actually moved at each step,
    and how much (word-level change ratio for the free blocks)."""
    d = OUTPUTS / run_name
    steps = []
    prev = None
    for p in sorted((d / "steps").glob("step_*.json")) if (d / "steps").is_dir() else []:
        try:
            detail = json.loads(p.read_text())
        except Exception:
            continue
        cur = detail.get("blocks") or {}
        cell = {}
        for f in FREE_BLOCKS:
            new, old = cur.get(f, ""), (prev or {}).get(f, "")
            if prev is None:
                cell[f] = {"changed": None, "ratio": None, "words": len(new.split())}
            else:
                ratio = difflib.SequenceMatcher(None, old.split(), new.split(),
                                                autojunk=False).ratio()
                cell[f] = {"changed": old != new, "ratio": round(1 - ratio, 3),
                           "words": len(new.split())}
        for f in POOL_BLOCKS:
            cell[f] = {"changed": None if prev is None else (prev or {}).get(f) != cur.get(f),
                       "value": cur.get(f), "ratio": None}
        steps.append({"step": detail.get("step"), "score": detail.get("score"),
                      "cb_ok": detail.get("cb_ok"), "cells": cell})
        prev = cur
    return {"steps": steps, "fields": list(FREE_BLOCKS) + list(POOL_BLOCKS)}


# --------------------------------------------------------------------------- #
# Routes                                                                       #
# --------------------------------------------------------------------------- #
@app.route("/")
def home():
    return send_from_directory(HERE, "index.html")


@app.route("/api/runs")
def api_runs():
    return jsonify({"runs": scan_runs(), "outputs": str(OUTPUTS)})


@app.route("/api/run")
def api_run():
    return jsonify(load_run(request.args.get("run", "")))


@app.route("/api/step")
def api_step():
    try:
        step = int(request.args["step"])
    except (KeyError, ValueError):
        return jsonify({"error": "step required"}), 400
    return jsonify(load_step(request.args.get("run", ""), step))


@app.route("/api/export")
def api_export():
    """One assistant session (or the whole rollout) as a downloadable .md file."""
    a = request.args
    try:
        step, seed = int(a["step"]), int(a["seed"])
    except (KeyError, ValueError):
        return jsonify({"error": "step and seed required"}), 400
    try:
        fname, md = export_session(a.get("run", ""), step, seed, a.get("agent", "__all__"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    return app.response_class(
        md, mimetype="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.route("/api/lies")
def api_lies():
    return jsonify(load_lies(request.args.get("run", "")))


@app.route("/api/blocks")
def api_blocks():
    return jsonify(load_blocks_evolution(request.args.get("run", "")))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5003)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    print(f"social_jira4 pathway viewer → http://{args.host}:{args.port}  (Ctrl-C to stop)")
    print(f"Scanning optimizer runs at: {OUTPUTS}")
    app.run(host=args.host, port=args.port, debug=False)
