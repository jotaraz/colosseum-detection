"""Web server for the job_appl MVP.

Run with:  uv run uvicorn server:app --reload --port 8642
(from experiments/job_appl; needs OPENROUTER_API_KEY in env or repo-root .env)

The frontend drives the run: it POSTs /api/run/<id>/step in a loop while the
run is "running"; each step performs one full application (two LLM calls) and
blocks until done. Check-ins pause the loop until the user resumes.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import engine
import world as world_module

app = FastAPI(title="job_appl")

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


class NewRun(BaseModel):
    name: str = ""
    traits: List[str] = Field(default_factory=list)
    skills: List[str] = Field(default_factory=list)
    max_tries: int = engine.DEFAULT_MAX_TRIES


class ChatMessage(BaseModel):
    message: str


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/world")
def api_world() -> Dict[str, Any]:
    try:
        return get_world()
    except Exception as exc:  # missing API key, generation failure, ...
        raise HTTPException(status_code=503, detail=str(exc))


@app.post("/api/run")
def api_new_run(body: NewRun) -> Dict[str, Any]:
    world = get_world()
    known_traits = set(world["traits"])
    known_skills = {s["name"] for s in world["skills"]}
    traits = [t for t in body.traits if t in known_traits]
    skills = [s for s in body.skills if s in known_skills]
    state = engine.new_run(world, traits, skills, max_tries=body.max_tries,
                           name=body.name)
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


@app.post("/api/run/{run_id}/chat")
def api_chat(run_id: str, body: ChatMessage) -> Dict[str, Any]:
    state = get_run(run_id)
    with _run_locks[run_id]:
        return engine.user_message(state, body.message.strip())


@app.post("/api/run/{run_id}/resume")
def api_resume(run_id: str) -> Dict[str, Any]:
    state = get_run(run_id)
    with _run_locks[run_id]:
        return engine.resume(state)
