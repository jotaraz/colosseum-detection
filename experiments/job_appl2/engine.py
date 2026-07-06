"""Run engine for the job_appl2 MVP.

Second MVP: the *human job seeker is replaced by an LLM* (the "client" /
user-LLM) that role-plays a person, with a real human kept in the loop as its
overseer. Versus job_appl:

  1. At setup the human picks (or free-texts) the person's traits and skills.
     A CV-writer LLM turns the skills + experiences into a detailed, realistic
     professional CV (26-year-old, career since 18) in Markdown, which the
     human can edit. The agent writes cover letters from that CV plus the
     person's lifestyle/personality traits (which are kept off the CV).
  2. The every-3rd-rejection check-in still happens: the client LLM drafts the
     reply, and a real human must sign off on it (and may edit / regenerate it)
     before it reaches the agent and is folded into its memory.

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
CHECKIN_EVERY = 3
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
    """A call to the client / user-LLM that role-plays the human job seeker."""
    system = prompts.user_llm_system(state["person"], state["director_prompt"])
    content, _ = _llm(state, role, [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ], USER_MODEL)
    return content.strip()


# --------------------------------------------------------------- CV writer

def generate_cv(
    name: str,
    skills: List[Dict[str, Any]],
    director_prompt: str = "",
) -> str:
    """One-off: turn the picked/custom skills into a detailed Markdown CV. Runs
    before a run exists, so it is not tied to a run's event log; the run records
    the final (human-approved) CV at creation."""
    name = name.strip() or "the applicant"
    content, _usage, _reasoning = openrouter.chat(
        [
            {"role": "system", "content": prompts.CV_GEN_SYSTEM},
            {"role": "user", "content": prompts.cv_gen_user(
                name, skills, director_prompt, datetime.now().year)},
        ],
        model=CV_MODEL,
        temperature=0.7,
        max_tokens=4096,
    )
    return content.strip()


# -------------------------------------------------------------- run engine

FIRST_NAMES = ["Alex", "Sam", "Jordan", "Robin", "Casey", "Jamie", "Morgan",
               "Taylor", "Riley", "Quinn", "Noor", "Luca", "Mina", "Ravi"]
LAST_NAMES = ["Berger", "Okafor", "Lindqvist", "Marino", "Novak", "Tanaka",
              "Whitfield", "Osei", "Kaur", "Fischer", "Delgado", "Petrov"]


def new_run(
    traits: List[str],
    skills: List[Dict[str, Any]],
    cv: str,
    max_tries: int = DEFAULT_MAX_TRIES,
    name: str = "",
    director_prompt: str = "",
    cv_original: str = "",
) -> Dict[str, Any]:
    name = name.strip() or f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
    cv = cv.strip()
    cv_original = (cv_original or cv).strip()
    state: Dict[str, Any] = {
        "run_id": datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6],
        "created_at": _now(),
        "status": "running",
        "person": {"name": name, "traits": traits, "skills": skills, "cv": cv},
        "cv_original": cv_original,
        "cv_edited": cv != cv_original,
        "director_prompt": director_prompt.strip(),
        "max_tries": int(max_tries),
        "applications": [],
        "rejections": 0,
        "feedback": [],
        "chat": [],
        "pending_draft": None,
        "final_report": None,
        "error": None,
    }
    _log(state, "run_created", person=state["person"],
         cv_original=state["cv_original"], cv_edited=state["cv_edited"],
         director_prompt=state["director_prompt"], max_tries=state["max_tries"],
         agent_model=AGENT_MODEL, hr_model=HR_MODEL, user_model=USER_MODEL,
         cv_model=CV_MODEL)
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
    """Run one application (agent picks target + writes letter, HR judges)."""
    if state["status"] != "running":
        return state

    i = len(state["applications"]) + 1
    tries_left = state["max_tries"] - len(state["applications"])

    # --- the agent picks a target and writes the letter (from the CV + traits)
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
        if state["rejections"] % CHECKIN_EVERY == 0:
            _checkin(state)

    _save_state(state)
    return state


def _checkin(state: Dict[str, Any]) -> None:
    """Agent reports to the client; the client LLM drafts a reply that a human
    must then sign off on. Leaves the run in ``awaiting_signoff``."""
    report, _ = _llm(state, "agent_checkin", [
        {"role": "system", "content": prompts.AGENT_SYSTEM},
        {"role": "user", "content": prompts.agent_checkin_user(
            state["applications"], CHECKIN_EVERY,
            state["max_tries"] - len(state["applications"]))},
    ], AGENT_MODEL)
    state["chat"].append({"role": "agent", "text": report,
                          "after_try": len(state["applications"])})

    draft = _user_llm(state, "user_checkin", prompts.user_llm_checkin_user(report))
    state["pending_draft"] = draft
    state["status"] = "awaiting_signoff"
    _log(state, "checkin", report=report, draft=draft)


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


def regenerate_draft(state: Dict[str, Any]) -> Dict[str, Any]:
    """Re-sample the client LLM's check-in reply (human wants a different draft
    to sign off on). Only valid while awaiting sign-off."""
    if state["status"] != "awaiting_signoff":
        return state
    report = next((m["text"] for m in reversed(state["chat"])
                   if m["role"] == "agent"
                   and m["after_try"] == len(state["applications"])), "")
    draft = _user_llm(state, "user_checkin", prompts.user_llm_checkin_user(report))
    state["pending_draft"] = draft
    _log(state, "regenerate_draft", draft=draft)
    _save_state(state)
    return state


def signoff(state: Dict[str, Any], message: str) -> Dict[str, Any]:
    """The human signs off on (optionally edited) the client's check-in reply.
    The approved message is sent to the agent and folded into its memory; the
    agent replies and applications resume."""
    if state["status"] != "awaiting_signoff":
        return state
    approved = message.strip() or (state.get("pending_draft") or "").strip()
    edited = approved != (state.get("pending_draft") or "").strip()
    state["chat"].append({"role": "user", "text": approved,
                          "after_try": len(state["applications"])})
    state["feedback"].append(approved)
    state["pending_draft"] = None
    _log(state, "signoff", approved=approved, edited=edited)

    current_checkin = [m for m in state["chat"]
                       if m["after_try"] == len(state["applications"])]
    reply, _ = _llm(state, "agent_chat", [
        {"role": "system", "content": prompts.AGENT_SYSTEM},
        {"role": "user", "content": prompts.agent_chat_user(current_checkin)},
    ], AGENT_MODEL)
    state["chat"].append({"role": "agent", "text": reply,
                          "after_try": len(state["applications"])})
    _log(state, "agent_chat", text=reply)

    state["status"] = "running"
    _log(state, "resume")
    _save_state(state)
    return state


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
    W(f"job_appl2 transcript — run {run_id}")
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
            W(f"Traits (off-CV, given to the agent): "
              f"{', '.join(p.get('traits') or []) or '(none)'}")
            skills = p.get("skills") or []
            W("Skills: " + (", ".join(s.get("name", "") for s in skills) or "(none)"))
            W(f"Director prompt: {e.get('director_prompt') or '(none)'}")
            W(f"CV edited by human: {'yes' if e.get('cv_edited') else 'no'}")
            W(f"Max applications: {e.get('max_tries')}")
            W(f"Models — agent: {e.get('agent_model')}, seeker: {e.get('user_model')}, "
              f"CV: {e.get('cv_model')}, HR: {e.get('hr_model')}")
            W("")
            W("CV (as used by the agent):")
            W(_indent(p.get("cv", "")))
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
            W("CHECK-IN")
            W(sep)
            W("Agent → seeker (report):")
            W(_indent(e.get("report", "")))
            W("")
            W("Seeker draft reply (awaiting human sign-off):")
            W(_indent(e.get("draft", "")))
        elif t == "regenerate_draft":
            W("")
            W("[human regenerated the draft]")
            W(_indent(e.get("draft", "")))
        elif t == "signoff":
            W("")
            W(f"Human sign-off (edited the draft: {'yes' if e.get('edited') else 'no'}):")
            W(_indent(e.get("approved", "")))
        elif t == "agent_chat":
            W("")
            W("Agent reply to seeker:")
            W(_indent(e.get("text", "")))
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
