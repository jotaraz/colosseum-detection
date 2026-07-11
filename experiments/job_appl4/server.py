"""Web server for the job_appl4 MVP.

Run with:  uv run uvicorn server:app --reload --port 8645
(from experiments/job_appl4; needs OPENROUTER_API_KEY in env or repo-root .env)

Setup flow: the human picks/free-texts traits + skills, writes an optional
client system prompt (steers the client LLM's comments only), and can script
individual check-in messages (a fixed message keyed by 1-based check-in index,
e.g. the 5th) — then starts the run (POST /api/run), which generates the
person's complete dossier and returns. From there the environment runs by
itself: the frontend POSTs /api/run/<id>/step in a loop while the run is
"running"; every n-th rejection the agent reports and the client (LLM or
script) comments inline (no human gate, no pause).

For batch sweeps over (skill, client prompt, scripted message) triples use
run_batch.py instead — same engine, no server needed.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

import engine
import openrouter
import world as world_module

app = FastAPI(title="job_appl4")

STATIC_DIR = Path(__file__).resolve().parent / "static"

_world: Optional[Dict[str, Any]] = None
_world_lock = threading.Lock()
_runs: Dict[str, Dict[str, Any]] = {}
_run_locks: Dict[str, threading.Lock] = {}


def get_world() -> Dict[str, Any]:
    global _world
    with _world_lock:
        if _world is None:
            _world = world_module.load_world()  # generates via LLM on first ever call
        return _world


def get_run(run_id: str) -> Dict[str, Any]:
    state = _runs.get(run_id) or engine.load_run(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id}")
    _runs[run_id] = state
    _run_locks.setdefault(run_id, threading.Lock())
    return state


class Skill(BaseModel):
    name: str
    experiences: List[str] = Field(default_factory=list)


def _clean_skills(skills: List[Skill]) -> List[Dict[str, Any]]:
    out = []
    for s in skills:
        name = s.name.strip()
        if name:
            out.append({"name": name,
                        "experiences": [e.strip() for e in s.experiences if e.strip()]})
    return out


def _clean_traits(traits: List[str]) -> List[str]:
    seen, out = set(), []
    for t in traits:
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


class NewRun(BaseModel):
    name: str = ""
    traits: List[str] = Field(default_factory=list)
    skills: List[Skill] = Field(default_factory=list)
    client_prompt: str = ""
    # 1-based check-in index -> fixed client message (JSON keys are strings)
    scripted_messages: Dict[str, str] = Field(default_factory=dict)
    max_tries: int = engine.DEFAULT_MAX_TRIES
    checkin_every: int = engine.DEFAULT_CHECKIN_EVERY
    agent_model: str = ""
    hr_model: str = ""
    client_model: str = ""
    cv_model: str = ""  # dossier writer; "" = env default (gpt-oss-120b)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/world")
def api_world() -> Dict[str, Any]:
    try:
        return get_world()
    except Exception as exc:  # missing API key, generation failure, ...
        raise HTTPException(status_code=503, detail=str(exc))


@app.get("/api/models")
def api_models() -> Dict[str, Any]:
    """OpenRouter's model catalog, plus the default model, for the setup
    dropdowns that pick each role's LLM."""
    try:
        return {"models": openrouter.list_models(), "default": engine.DEFAULT_MODEL}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/run")
def api_new_run(body: NewRun) -> Dict[str, Any]:
    try:
        state = engine.new_run(
            _clean_traits(body.traits), _clean_skills(body.skills),
            max_tries=body.max_tries, name=body.name,
            client_prompt=body.client_prompt,
            scripted_messages=body.scripted_messages,
            checkin_every=body.checkin_every,
            agent_model=body.agent_model, hr_model=body.hr_model,
            client_model=body.client_model, cv_model=body.cv_model)
    except ValueError as exc:  # bad scripted_messages keys
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:  # dossier generation failure, missing API key, ...
        raise HTTPException(status_code=503, detail=str(exc))
    _runs[state["run_id"]] = state
    _run_locks[state["run_id"]] = threading.Lock()
    return state


@app.get("/api/run/{run_id}")
def api_get_run(run_id: str) -> Dict[str, Any]:
    return get_run(run_id)


@app.post("/api/run/{run_id}/step")
def api_step(run_id: str) -> Dict[str, Any]:
    state = get_run(run_id)
    lock = _run_locks[run_id]
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="a step is already in progress")
    try:
        return engine.step(state, get_world())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        lock.release()


@app.get("/api/run/{run_id}/transcript")
def api_transcript(run_id: str) -> PlainTextResponse:
    state = get_run(run_id)
    text = engine.transcript_text(state)
    filename = f"transcript_{run_id}.txt"
    return PlainTextResponse(
        content=text,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
