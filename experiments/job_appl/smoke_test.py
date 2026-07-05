"""Offline smoke test: stub the LLM and drive a full run through the API.

Exercises: world loading, run creation, rejection loop, the every-3rd-rejection
check-in (report + chat + resume), an interview invitation ending the run, and
the MAX_TRIES failure path. No network calls; no OPENROUTER_API_KEY needed.

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


class FakeLLM:
    """Scripted stand-in for openrouter.chat, keyed off the prompt contents."""

    def __init__(self, hr_script: List[str]):
        self.hr_script = list(hr_script)  # "invite"/"reject" per HR call
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

    engine.RUNS_DIR = Path(tempfile.mkdtemp(prefix="job_appl_smoke_")) / "runs"

    # 2 rejections, check-in never fires yet; 3rd rejection -> check-in; then invite.
    fake = FakeLLM(hr_script=["reject", "reject", "reject", "invite"])
    openrouter.chat = fake.chat  # engine and world call through this module

    server._world = FAKE_WORLD  # skip world generation

    from fastapi.testclient import TestClient
    client = TestClient(server.app)

    world = client.get("/api/world").json()
    assert len(world["companies"]) == 4

    run = client.post("/api/run", json={
        "name": "Test Person",
        "traits": ["trait 1", "not-a-real-trait"], "skills": ["skill 2"],
        "max_tries": 10}).json()
    rid = run["run_id"]
    assert run["person"]["traits"] == ["trait 1"], "unknown traits must be filtered"
    assert run["person"]["name"] == "Test Person"
    assert run["person"]["skills"] == [
        {"name": "skill 2", "experiences": ["exp 2a", "exp 2b", "exp 2c"]}
    ], "picked skills must carry their world experiences"

    for expected_status in ("running", "running", "awaiting_user"):
        run = client.post(f"/api/run/{rid}/step").json()
        assert run["status"] == expected_status, (run["status"], expected_status)
    assert run["rejections"] == 3
    assert run["chat"][-1]["role"] == "agent", "check-in report missing"

    run = client.post(f"/api/run/{rid}/chat", json={"message": "aim smaller"}).json()
    assert run["feedback"] == ["aim smaller"]
    assert run["chat"][-1]["role"] == "agent", "agent chat reply missing"

    run = client.post(f"/api/run/{rid}/resume").json()
    assert run["status"] == "running"

    run = client.post(f"/api/run/{rid}/step").json()
    assert run["status"] == "success"
    assert run["final_report"] == "Final report."
    assert len(run["applications"]) == 4
    assert run["applications"][-1]["decision"] == "invite"
    assert run["applications"][-1]["agent_cot"] == "Fake agent CoT."

    # user advice must reach the next apply prompt
    apply_prompts = [c for c in fake.calls if c == "agent_apply"]
    assert len(apply_prompts) == 4

    # failure path: all rejections, max_tries=2 (no check-in before the cap)
    fake.hr_script = ["reject", "reject"]
    run = client.post("/api/run", json={"traits": [], "skills": [], "max_tries": 2}).json()
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
