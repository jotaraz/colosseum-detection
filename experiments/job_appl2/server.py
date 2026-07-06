"""Web server for the job_appl2 MVP.

Run with:  uv run uvicorn server:app --reload --port 8643
(from experiments/job_appl2; needs OPENROUTER_API_KEY in env or repo-root .env)

Setup flow: the human picks/free-texts traits + skills, a CV-writer LLM turns
the skills into a Markdown CV (POST /api/cv), the human edits/regenerates it,
then starts the run (POST /api/run) with the finalized CV. The frontend then
POSTs /api/run/<id>/step in a loop while the run is "running"; every 3rd
rejection the client LLM drafts a check-in reply and the run pauses in
"awaiting_signoff" until the human signs it off via /signoff.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

import engine
import world as world_module

app = FastAPI(title="job_appl2")

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


class CVRequest(BaseModel):
    name: str = ""
    skills: List[Skill] = Field(default_factory=list)
    director_prompt: str = ""


class NewRun(BaseModel):
    name: str = ""
    traits: List[str] = Field(default_factory=list)
    skills: List[Skill] = Field(default_factory=list)
    cv: str = ""
    cv_original: str = ""
    director_prompt: str = ""
    max_tries: int = engine.DEFAULT_MAX_TRIES


class SignOff(BaseModel):
    message: str = ""


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/world")
def api_world() -> Dict[str, Any]:
    try:
        return get_world()
    except Exception as exc:  # missing API key, generation failure, ...
        raise HTTPException(status_code=503, detail=str(exc))


@app.post("/api/cv")
def api_cv(body: CVRequest) -> Dict[str, str]:
    try:
        cv = engine.generate_cv(body.name, _clean_skills(body.skills),
                                director_prompt=body.director_prompt)
    except Exception as exc:  # missing API key, generation failure, ...
        raise HTTPException(status_code=503, detail=str(exc))
    return {"cv": cv}


@app.post("/api/run")
def api_new_run(body: NewRun) -> Dict[str, Any]:
    if not body.cv.strip():
        raise HTTPException(status_code=400, detail="a CV is required to start a run")
    state = engine.new_run(
        _clean_traits(body.traits), _clean_skills(body.skills), body.cv,
        max_tries=body.max_tries, name=body.name,
        director_prompt=body.director_prompt, cv_original=body.cv_original)
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


@app.post("/api/run/{run_id}/regenerate_draft")
def api_regenerate_draft(run_id: str) -> Dict[str, Any]:
    state = get_run(run_id)
    with _run_locks[run_id]:
        try:
            return engine.regenerate_draft(state)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/run/{run_id}/signoff")
def api_signoff(run_id: str, body: SignOff) -> Dict[str, Any]:
    state = get_run(run_id)
    with _run_locks[run_id]:
        try:
            return engine.signoff(state, body.message)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
