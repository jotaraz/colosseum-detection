"""Run engine for the job_appl3 MVP.

Third MVP: once set up, the run is fully autonomous. Versus job_appl2:

  1. The CV becomes a complete DOSSIER — a detailed professional CV plus a
     personal profile (every trait, lifestyle detail) plus practical details.
     It is generated at run creation (no human review/edit stage) and contains
     everything that exists about the person, so the agent never needs to ask.
  2. The every-n-th-rejection check-in still happens, but without any human
     gate: the agent reports, the client LLM (steered only by the human's
     one-off director system prompt) leaves a comment, the comment is folded
     into the agent's feedback, and applications continue immediately.

The human's controls are all at setup time: name, traits, skills, the director
system prompt, the check-in interval, and max tries. After "start" the
environment runs by itself.

A run is a plain dict (JSON-serializable). Every LLM call and event is appended
to ``outputs/runs/<run_id>/events.jsonl`` and the current state is mirrored to
``state.json`` for later analysis.
"""

from __future__ import annotations

import json
import os
import random
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import openrouter
import prompts

AGENT_MODEL = os.getenv("JOB_APPL_AGENT_MODEL", "openai/gpt-oss-120b")
HR_MODEL = os.getenv("JOB_APPL_HR_MODEL", "openai/gpt-oss-120b")
USER_MODEL = os.getenv("JOB_APPL_USER_MODEL", "openai/gpt-oss-120b")
CV_MODEL = os.getenv("JOB_APPL_CV_MODEL", "openai/gpt-oss-120b")
DEFAULT_CHECKIN_EVERY = 3
DEFAULT_MAX_TRIES = 30
JSON_PARSE_RETRIES = 2

RUNS_DIR = Path(__file__).resolve().parent / "outputs" / "runs"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------- logging

def _run_dir(state: Dict[str, Any]) -> Path:
    d = RUNS_DIR / state["run_id"]
    d.mkdir(parents=True, exist_ok=True)
    return d


def _log(state: Dict[str, Any], event_type: str, **payload: Any) -> None:
    event = {"ts": _now(), "type": event_type, **payload}
    with (_run_dir(state) / "events.jsonl").open("a") as f:
        f.write(json.dumps(event) + "\n")


def _save_state(state: Dict[str, Any]) -> None:
    (_run_dir(state) / "state.json").write_text(json.dumps(state, indent=2))


# --------------------------------------------------------------- LLM calls

def _llm(
    state: Dict[str, Any],
    role: str,
    messages: List[Dict[str, str]],
    model: str,
    temperature: float = 0.7,
) -> Tuple[str, str]:
    """Returns (content, reasoning/CoT)."""
    content, usage, reasoning = openrouter.chat(messages, model=model,
                                                temperature=temperature)
    _log(state, "llm_call", role=role, model=model, messages=messages,
         response=content, reasoning=reasoning, usage=usage)
    return content, reasoning


def _llm_json(
    state: Dict[str, Any],
    role: str,
    messages: List[Dict[str, str]],
    model: str,
    required_keys: List[str],
) -> Tuple[Dict[str, Any], str]:
    """Returns (parsed object, reasoning/CoT of the successful call)."""
    convo = list(messages)
    for _ in range(JSON_PARSE_RETRIES + 1):
        content, reasoning = _llm(state, role, convo, model)
        obj = openrouter.extract_json(content)
        if isinstance(obj, dict) and all(k in obj for k in required_keys):
            return obj, reasoning
        convo = convo + [
            {"role": "assistant", "content": content},
            {"role": "user", "content":
                f"That was not a valid JSON object with the keys {required_keys}. "
                "Respond again with only the JSON object."},
        ]
    raise RuntimeError(f"{role} failed to produce valid JSON after retries")


def _user_llm(
    state: Dict[str, Any],
    role: str,
    user_content: str,
) -> str:
    """A call to the client / user-LLM that role-plays the job seeker."""
    system = prompts.user_llm_system(state["person"], state["director_prompt"])
    content, _ = _llm(state, role, [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ], USER_MODEL)
    return content.strip()


# -------------------------------------------------------------- run engine

FIRST_NAMES = ["Alex", "Sam", "Jordan", "Robin", "Casey", "Jamie", "Morgan",
               "Taylor", "Riley", "Quinn", "Noor", "Luca", "Mina", "Ravi"]
LAST_NAMES = ["Berger", "Okafor", "Lindqvist", "Marino", "Novak", "Tanaka",
              "Whitfield", "Osei", "Kaur", "Fischer", "Delgado", "Petrov"]


def new_run(
    traits: List[str],
    skills: List[Dict[str, Any]],
    max_tries: int = DEFAULT_MAX_TRIES,
    name: str = "",
    director_prompt: str = "",
    checkin_every: int = DEFAULT_CHECKIN_EVERY,
) -> Dict[str, Any]:
    """Create a run and generate the person's complete dossier. The dossier
    LLM call is part of the run and lands in its event log."""
    name = name.strip() or f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
    state: Dict[str, Any] = {
        "run_id": datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6],
        "created_at": _now(),
        "status": "running",
        "person": {"name": name, "traits": traits, "skills": skills, "cv": ""},
        "director_prompt": director_prompt.strip(),
        "max_tries": int(max_tries),
        "checkin_every": max(0, int(checkin_every)),
        "applications": [],
        "rejections": 0,
        "feedback": [],
        "chat": [],
        "final_report": None,
        "error": None,
    }
    _log(state, "run_created", person={k: v for k, v in state["person"].items() if k != "cv"},
         director_prompt=state["director_prompt"], max_tries=state["max_tries"],
         checkin_every=state["checkin_every"],
         agent_model=AGENT_MODEL, hr_model=HR_MODEL, user_model=USER_MODEL,
         cv_model=CV_MODEL)

    dossier, _ = _llm(state, "dossier_writer", [
        {"role": "system", "content": prompts.CV_GEN_SYSTEM},
        {"role": "user", "content": prompts.cv_gen_user(
            name, traits, skills, state["director_prompt"],
            datetime.now().year)},
    ], CV_MODEL)
    state["person"]["cv"] = dossier.strip()
    _log(state, "dossier", dossier=state["person"]["cv"])
    _save_state(state)
    return state


def _find(world: Dict[str, Any], company_id: str, job_id: str):
    for company in world["companies"]:
        if company["id"] == company_id:
            for job in company["jobs"]:
                if job["id"] == job_id:
                    return company, job
    return None, None


def step(state: Dict[str, Any], world: Dict[str, Any]) -> Dict[str, Any]:
    """Run one application (agent picks target + writes letter, HR judges).
    Every ``checkin_every``-th rejection the check-in happens inline — agent
    report, client comment — and the run keeps going without pausing."""
    if state["status"] != "running":
        return state

    i = len(state["applications"]) + 1
    tries_left = state["max_tries"] - len(state["applications"])

    # --- the agent picks a target and writes the letter (from the dossier)
    apply_messages = [
        {"role": "system", "content": prompts.AGENT_SYSTEM},
        {"role": "user", "content": prompts.agent_apply_user(
            state["person"], world["companies"], state["applications"],
            state["feedback"], tries_left)},
    ]
    application, cot = _llm_json(state, "agent_apply", apply_messages, AGENT_MODEL,
                                 required_keys=["company_id", "job_id", "cover_letter"])
    company, job = _find(world, application.get("company_id"), application.get("job_id"))
    if company is None:
        # invalid target: nudge once with the valid ids, then give up on this try
        valid = {c["id"]: [j["id"] for j in c["jobs"]] for c in world["companies"]}
        apply_messages.append({"role": "assistant", "content": json.dumps(application)})
        apply_messages.append({"role": "user", "content":
            f"company_id/job_id do not exist. Valid options: {json.dumps(valid)}. "
            "Respond again with only the corrected JSON object."})
        application, cot = _llm_json(state, "agent_apply", apply_messages, AGENT_MODEL,
                                     required_keys=["company_id", "job_id", "cover_letter"])
        company, job = _find(world, application.get("company_id"), application.get("job_id"))
        if company is None:
            raise RuntimeError("agent picked a nonexistent company/job twice")

    # --- HR judges the letter (fresh context, no memory)
    hr_messages = [
        {"role": "system", "content": prompts.hr_system(company, job)},
        {"role": "user", "content": prompts.hr_user(application["cover_letter"])},
    ]
    verdict, _hr_cot = _llm_json(state, "hr", hr_messages, HR_MODEL,
                                 required_keys=["decision", "reasons"])
    decision = "invite" if str(verdict["decision"]).strip().lower() == "invite" else "reject"

    record = {
        "i": i,
        "company_id": company["id"],
        "company_name": company["name"],
        "job_id": job["id"],
        "job_title": job["title"],
        "agent_reasoning": application.get("reasoning", ""),
        "agent_cot": cot,
        "cover_letter": application["cover_letter"],
        "decision": decision,
        "reasons": str(verdict["reasons"]),
    }
    state["applications"].append(record)
    _log(state, "application", **record)

    # --- outcome routing
    if decision == "invite":
        state["status"] = "success"
        _final_report(state, success=True)
    elif len(state["applications"]) >= state["max_tries"]:
        state["status"] = "failed"
        _final_report(state, success=False)
    else:
        state["rejections"] += 1
        every = state.get("checkin_every", DEFAULT_CHECKIN_EVERY)
        if every > 0 and state["rejections"] % every == 0:
            _checkin(state)

    _save_state(state)
    return state


def _checkin(state: Dict[str, Any]) -> None:
    """Agent reports to the client; the client LLM leaves a comment (steered
    only by the director prompt) that goes straight into the agent's feedback.
    No human gate — the run keeps running."""
    every = state.get("checkin_every", DEFAULT_CHECKIN_EVERY)
    report, _ = _llm(state, "agent_checkin", [
        {"role": "system", "content": prompts.AGENT_SYSTEM},
        {"role": "user", "content": prompts.agent_checkin_user(
            state["applications"], every,
            state["max_tries"] - len(state["applications"]))},
    ], AGENT_MODEL)
    state["chat"].append({"role": "agent", "text": report,
                          "after_try": len(state["applications"])})

    comment = _user_llm(state, "user_checkin", prompts.user_llm_checkin_user(report))
    state["chat"].append({"role": "user", "text": comment,
                          "after_try": len(state["applications"])})
    state["feedback"].append(comment)
    _log(state, "checkin", report=report, comment=comment)


def _final_report(state: Dict[str, Any], success: bool) -> None:
    report, _ = _llm(state, "agent_final", [
        {"role": "system", "content": prompts.AGENT_SYSTEM},
        {"role": "user", "content": prompts.agent_final_user(
            state["applications"], success)},
    ], AGENT_MODEL)
    state["final_report"] = report
    state["chat"].append({"role": "agent", "text": report,
                          "after_try": len(state["applications"])})
    _log(state, "final", success=success, report=report)


def load_run(run_id: str) -> Optional[Dict[str, Any]]:
    path = RUNS_DIR / run_id / "state.json"
    if path.is_file():
        return json.loads(path.read_text())
    return None


# ------------------------------------------------------------- transcript

def _indent(text: str, pad: str = "    ") -> str:
    text = text or ""
    return "\n".join(pad + ln for ln in text.splitlines()) or (pad + "(empty)")


def transcript_text(state: Dict[str, Any]) -> str:
    """Render a human-readable transcript of the whole run from its event log
    (falls back to the in-memory state if the log is missing)."""
    run_id = state["run_id"]
    events: List[Dict[str, Any]] = []
    events_path = RUNS_DIR / run_id / "events.jsonl"
    if events_path.is_file():
        for line in events_path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    lines: List[str] = []
    W = lines.append
    sep = "=" * 70
    W(sep)
    W(f"job_appl3 transcript — run {run_id}")
    W(f"created: {state.get('created_at', '')}")
    W(f"status:  {state.get('status', '')}")
    W(sep)

    for e in events:
        t = e.get("type")
        if t == "run_created":
            p = e.get("person", {})
            W("")
            W("SETUP")
            W(f"Job seeker: {p.get('name', '')}")
            W(f"Traits: {', '.join(p.get('traits') or []) or '(none)'}")
            skills = p.get("skills") or []
            W("Skills: " + (", ".join(s.get("name", "") for s in skills) or "(none)"))
            W(f"Director prompt (the human's only steering during the run): "
              f"{e.get('director_prompt') or '(none)'}")
            W(f"Max applications: {e.get('max_tries')}, "
              f"check-in every {e.get('checkin_every')} rejection(s)")
            W(f"Models — agent: {e.get('agent_model')}, seeker: {e.get('user_model')}, "
              f"dossier: {e.get('cv_model')}, HR: {e.get('hr_model')}")
        elif t == "dossier":
            W("")
            W("DOSSIER (the agent's single source of truth about the person):")
            W(_indent(e.get("dossier", "")))
        elif t == "application":
            W("")
            W(sep)
            W(f"APPLICATION #{e['i']} → {e['company_name']} — {e['job_title']}")
            W(sep)
            if e.get("agent_reasoning"):
                W(f"Agent reasoning: {e['agent_reasoning']}")
            if e.get("agent_cot"):
                W("")
                W("Agent chain-of-thought:")
                W(_indent(e["agent_cot"]))
            W("")
            W("Cover letter:")
            W(_indent(e.get("cover_letter", "")))
            W("")
            W(f"HR decision: {str(e.get('decision', '')).upper()}")
            W(f"HR reasons:  {e.get('reasons', '')}")
        elif t == "checkin":
            W("")
            W(sep)
            W("CHECK-IN (autonomous — no human sign-off)")
            W(sep)
            W("Agent → client (report):")
            W(_indent(e.get("report", "")))
            W("")
            W("Client comment (LLM, steered by the director prompt):")
            W(_indent(e.get("comment", "")))
        elif t == "final":
            W("")
            W(sep)
            W("FINAL — " + ("SUCCESS (interview!)" if e.get("success") else "FAILED (gave up)"))
            W(sep)
            W(_indent(e.get("report", "")))

    if not events:
        W("")
        W("(no event log found for this run)")
    W("")
    return "\n".join(lines)
