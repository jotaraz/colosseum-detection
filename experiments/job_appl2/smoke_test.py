"""Offline smoke test: stub the LLM and drive a full run through the API.

Exercises: world loading, CV generation (POST /api/cv) + a human edit, run
creation from the CV, the rejection loop, the every-3rd-rejection check-in
(agent report + client-LLM draft + regenerate + human sign-off with an edit),
an interview invitation ending the run, and the MAX_TRIES failure path. No
network calls; no OPENROUTER_API_KEY needed.

    uv run python smoke_test.py
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

FAKE_WORLD = {
    "traits": [f"trait {i}" for i in range(25)],
    "skills": [{"name": f"skill {i}",
                "experiences": [f"exp {i}a", f"exp {i}b", f"exp {i}c"]}
               for i in range(20)],
    "companies": [
        {
            "id": f"co-{i}", "name": f"Company {i}", "industry": "testing",
            "products": "Widgets.", "philosophy": "Move fast.",
            "jobs": [{"id": f"job-{i}", "title": f"Widgeteer {i}",
                      "description": "Make widgets."}],
        }
        for i in range(4)
    ],
}

FAKE_CV = "# Test Person\n\n## Experience\n- Widgeteer, 2020-present\n"


class FakeLLM:
    """Scripted stand-in for openrouter.chat, keyed off the prompt contents."""

    def __init__(self, hr_script: List[str]):
        self.hr_script = list(hr_script)  # "invite"/"reject" per HR call
        self.regens = 0
        self.calls: List[str] = []

    def chat(self, messages, model, temperature=0.7, max_tokens=8192) -> Tuple[str, Dict[str, Any], str]:
        system = messages[0]["content"]
        user = messages[-1]["content"]
        usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
        if "HR AI-agent" in system:
            self.calls.append("hr")
            decision = self.hr_script.pop(0)
            return json.dumps({"decision": decision, "reasons": f"Scripted {decision}."}), usage, "Fake HR CoT."
        if "world-builder" in system:
            self.calls.append("world")
            return json.dumps(FAKE_WORLD), usage, ""
        if "resume writer" in system:
            self.calls.append("cv")
            return FAKE_CV, usage, ""
        if "role-playing a REAL human" in system:
            self.calls.append("user_checkin")
            self.regens += 1
            return f"Draft advice #{self.regens}: keep at it.", usage, ""
        # agent role, disambiguate by the user prompt
        if "Pick ONE company" in user:
            self.calls.append("agent_apply")
            return json.dumps({
                "company_id": "co-1", "job_id": "job-1",
                "reasoning": "Scripted pick.", "cover_letter": "Dear HR, hire my client.",
            }), usage, "Fake agent CoT."
        if "time to check in" in user:
            self.calls.append("agent_checkin")
            return "Check-in report: three rejections, adjusting course.", usage, ""
        if "mid-conversation" in user:
            self.calls.append("agent_chat")
            return "Got it, will do.", usage, ""
        self.calls.append("agent_final")
        return "Final report.", usage, ""


def main() -> None:
    import tempfile
    from pathlib import Path

    import engine
    import openrouter
    import server

    engine.RUNS_DIR = Path(tempfile.mkdtemp(prefix="job_appl2_smoke_")) / "runs"

    # 3 rejections -> check-in/sign-off; then invite.
    fake = FakeLLM(hr_script=["reject", "reject", "reject", "invite"])
    openrouter.chat = fake.chat  # engine and world call through this module

    server._world = FAKE_WORLD  # skip world generation

    from fastapi.testclient import TestClient
    client = TestClient(server.app)

    world = client.get("/api/world").json()
    assert len(world["companies"]) == 4

    # CV generation from picked + custom skills
    cv = client.post("/api/cv", json={
        "name": "Test Person",
        "skills": [{"name": "skill 2", "experiences": ["exp 2a"]},
                   {"name": "TIG welding", "experiences": ["Certified welder since 2019"]}],
        "director_prompt": "You are modest and honest."}).json()["cv"]
    assert cv == FAKE_CV.strip(), cv
    assert "cv" in fake.calls, "CV writer must be invoked"

    # human edits the CV before starting
    edited_cv = cv + "\n## Note\n- Reviewed by a human.\n"
    run = client.post("/api/run", json={
        "name": "Test Person",
        "traits": ["trait 1", "night owl"], "skills": [{"name": "skill 2", "experiences": ["exp 2a"]}],
        "cv": edited_cv, "cv_original": cv,
        "director_prompt": "You are modest and honest.",
        "max_tries": 10}).json()
    rid = run["run_id"]
    assert run["person"]["name"] == "Test Person"
    assert run["person"]["traits"] == ["trait 1", "night owl"], "custom traits must pass through"
    assert run["person"]["cv"] == edited_cv.strip()
    assert run["cv_edited"] is True, "editing the CV must be recorded"
    assert run["director_prompt"] == "You are modest and honest."

    # a run cannot start without a CV
    assert client.post("/api/run", json={"cv": "   "}).status_code == 400

    for expected_status in ("running", "running", "awaiting_signoff"):
        run = client.post(f"/api/run/{rid}/step").json()
        assert run["status"] == expected_status, (run["status"], expected_status)
    assert run["rejections"] == 3
    assert "qa" not in run["applications"][0], "the per-letter Q&A step is gone"
    assert run["chat"][-1]["role"] == "agent", "check-in report missing"
    assert run["pending_draft"] and run["pending_draft"].startswith("Draft advice"), \
        "client LLM must draft a check-in reply for sign-off"

    # human asks for a fresh draft, then signs off with an edited message
    draft1 = run["pending_draft"]
    run = client.post(f"/api/run/{rid}/regenerate_draft").json()
    assert run["status"] == "awaiting_signoff"
    assert run["pending_draft"] != draft1, "regenerate must resample the draft"

    run = client.post(f"/api/run/{rid}/signoff",
                      json={"message": "aim smaller — I'll take a junior role"}).json()
    assert run["feedback"] == ["aim smaller — I'll take a junior role"], \
        "the human's edited (not the LLM's) text must reach the agent"
    assert run["pending_draft"] is None
    assert run["chat"][-1]["role"] == "agent", "agent chat reply missing"
    assert run["status"] == "running", "sign-off resumes applications"

    run = client.post(f"/api/run/{rid}/step").json()
    assert run["status"] == "success"
    assert run["final_report"] == "Final report."
    assert len(run["applications"]) == 4
    assert run["applications"][-1]["decision"] == "invite"
    assert run["applications"][-1]["agent_cot"] == "Fake agent CoT."

    # the human's advice must reach the next apply prompt (folded into feedback)
    assert "aim smaller" in run["feedback"][0]

    # transcript renders and includes the CV
    tx = client.get(f"/api/run/{rid}/transcript").text
    assert "job_appl2 transcript" in tx and "CV (as used by the agent)" in tx
    assert "Reviewed by a human." in tx and "CV edited by human: yes" in tx

    # failure path: all rejections, max_tries=2 (no check-in before the cap)
    fake.hr_script = ["reject", "reject"]
    run = client.post("/api/run", json={
        "traits": [], "skills": [], "cv": "# Nobody\n", "max_tries": 2}).json()
    rid = run["run_id"]
    assert run["person"]["name"], "empty name must be auto-generated"
    run = client.post(f"/api/run/{rid}/step").json()
    assert run["status"] == "running"
    run = client.post(f"/api/run/{rid}/step").json()
    assert run["status"] == "failed" and run["final_report"] == "Final report."

    # log files exist
    log_dir = engine.RUNS_DIR / rid
    events = [json.loads(line) for line in (log_dir / "events.jsonl").read_text().splitlines()]
    assert any(e["type"] == "llm_call" for e in events)
    assert (log_dir / "state.json").is_file()

    print("smoke test passed:", len(fake.calls), "LLM calls simulated")


if __name__ == "__main__":
    main()
