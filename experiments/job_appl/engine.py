"""Run engine for the job_appl MVP.

A run is a plain dict (JSON-serializable) advanced by ``step()`` one
application at a time: the agent LLM picks a company and writes a cover
letter, the HR LLM of that company judges it. Every LLM call and event is
appended to ``outputs/runs/<run_id>/events.jsonl`` and the current state is
mirrored to ``state.json`` for later analysis.
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


# -------------------------------------------------------------- run engine

FIRST_NAMES = ["Alex", "Sam", "Jordan", "Robin", "Casey", "Jamie", "Morgan",
               "Taylor", "Riley", "Quinn", "Noor", "Luca", "Mina", "Ravi"]
LAST_NAMES = ["Berger", "Okafor", "Lindqvist", "Marino", "Novak", "Tanaka",
              "Whitfield", "Osei", "Kaur", "Fischer", "Delgado", "Petrov"]


def new_run(
    world: Dict[str, Any],
    traits: List[str],
    skills: List[str],
    max_tries: int = DEFAULT_MAX_TRIES,
    name: str = "",
) -> Dict[str, Any]:
    name = name.strip() or f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
    # skills arrive as names; store the full world objects (with experiences)
    picked = set(skills)
    skill_objs = [s for s in world["skills"] if s["name"] in picked]
    state: Dict[str, Any] = {
        "run_id": datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6],
        "created_at": _now(),
        "status": "running",
        "person": {"name": name, "traits": traits, "skills": skill_objs},
        "max_tries": int(max_tries),
        "applications": [],
        "rejections": 0,
        "feedback": [],
        "chat": [],
        "final_report": None,
        "error": None,
    }
    _log(state, "run_created", person=state["person"], max_tries=state["max_tries"],
         agent_model=AGENT_MODEL, hr_model=HR_MODEL)
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

    # --- the agent picks a target and writes the letter
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
    report, _ = _llm(state, "agent_checkin", [
        {"role": "system", "content": prompts.AGENT_SYSTEM},
        {"role": "user", "content": prompts.agent_checkin_user(
            state["applications"], CHECKIN_EVERY,
            state["max_tries"] - len(state["applications"]))},
    ], AGENT_MODEL)
    state["status"] = "awaiting_user"
    state["chat"].append({"role": "agent", "text": report,
                          "after_try": len(state["applications"])})
    _log(state, "checkin", report=report)


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


def user_message(state: Dict[str, Any], text: str) -> Dict[str, Any]:
    """User replies during a check-in; the agent answers in the chat and the
    advice is folded into the agent's memory for future applications."""
    if state["status"] != "awaiting_user":
        return state
    state["chat"].append({"role": "user", "text": text,
                          "after_try": len(state["applications"])})
    state["feedback"].append(text)
    _log(state, "user_message", text=text)

    current_checkin = [m for m in state["chat"]
                       if m["after_try"] == len(state["applications"])]
    reply, _ = _llm(state, "agent_chat", [
        {"role": "system", "content": prompts.AGENT_SYSTEM},
        {"role": "user", "content": prompts.agent_chat_user(current_checkin)},
    ], AGENT_MODEL)
    state["chat"].append({"role": "agent", "text": reply,
                          "after_try": len(state["applications"])})
    _log(state, "agent_chat", text=reply)
    _save_state(state)
    return state


def resume(state: Dict[str, Any]) -> Dict[str, Any]:
    """User closes the check-in chat; applications continue."""
    if state["status"] == "awaiting_user":
        state["status"] = "running"
        _log(state, "resume")
        _save_state(state)
    return state


def load_run(run_id: str) -> Optional[Dict[str, Any]]:
    path = RUNS_DIR / run_id / "state.json"
    if path.is_file():
        return json.loads(path.read_text())
    return None
