"""Offline smoke test: stub the LLM and drive a full run through the API.

Exercises: world loading, run creation (which generates the dossier — no human
review), the rejection loop, the autonomous every-n-th-rejection check-in
(agent report + client-LLM comment folded straight into feedback, run keeps
running), an interview invitation ending the run, the MAX_TRIES failure path,
and checkin_every=0 (never check in). No network calls; no OPENROUTER_API_KEY
needed.

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

FAKE_DOSSIER = ("# Test Person\n\n## Experience\n- Widgeteer, 2020-present\n\n"
                "## Personal Profile\n- Plays chess.\n")


class FakeLLM:
    """Scripted stand-in for openrouter.chat, keyed off the prompt contents."""

    def __init__(self, hr_script: List[str]):
        self.hr_script = list(hr_script)  # "invite"/"reject" per HR call
        self.comments = 0
        self.calls: List[str] = []
        self.last_apply_prompt = ""

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
        if "profile writer" in system:
            self.calls.append("dossier")
            return FAKE_DOSSIER, usage, ""
        if "role-playing a REAL human" in system:
            self.calls.append("user_checkin")
            self.comments += 1
            return f"Comment #{self.comments}: aim smaller.", usage, ""
        # agent role, disambiguate by the user prompt
        if "Pick ONE company" in user:
            self.calls.append("agent_apply")
            self.last_apply_prompt = user
            return json.dumps({
                "company_id": "co-1", "job_id": "job-1",
                "reasoning": "Scripted pick.", "cover_letter": "Dear HR, hire my client.",
            }), usage, "Fake agent CoT."
        if "time to report" in user:
            self.calls.append("agent_checkin")
            return "Check-in report: three rejections, adjusting course.", usage, ""
        self.calls.append("agent_final")
        return "Final report.", usage, ""


def main() -> None:
    import tempfile
    from pathlib import Path

    import engine
    import openrouter
    import server

    engine.RUNS_DIR = Path(tempfile.mkdtemp(prefix="job_appl3_smoke_")) / "runs"

    # 3 rejections -> autonomous check-in; then invite.
    fake = FakeLLM(hr_script=["reject", "reject", "reject", "invite"])
    openrouter.chat = fake.chat  # engine and world call through this module

    server._world = FAKE_WORLD  # skip world generation

    from fastapi.testclient import TestClient
    client = TestClient(server.app)

    world = client.get("/api/world").json()
    assert len(world["companies"]) == 4

    # run creation generates the dossier — no separate CV endpoint, no review
    run = client.post("/api/run", json={
        "name": "Test Person",
        "traits": ["trait 1", "night owl"],
        "skills": [{"name": "skill 2", "experiences": ["exp 2a"]},
                   {"name": "TIG welding", "experiences": ["Certified welder since 2019"]}],
        "director_prompt": "Push the agent toward small companies.",
        "max_tries": 10, "checkin_every": 3}).json()
    rid = run["run_id"]
    assert run["person"]["name"] == "Test Person"
    assert run["person"]["traits"] == ["trait 1", "night owl"], "custom traits must pass through"
    assert run["person"]["cv"] == FAKE_DOSSIER.strip(), "dossier must be generated at run creation"
    assert "dossier" in fake.calls, "dossier writer must be invoked"
    assert run["director_prompt"] == "Push the agent toward small companies."
    assert run["status"] == "running"

    # 3 rejections: the check-in happens inline and the run NEVER pauses
    for _ in range(3):
        run = client.post(f"/api/run/{rid}/step").json()
        assert run["status"] == "running", run["status"]
    assert run["rejections"] == 3
    assert "user_checkin" in fake.calls, "client LLM must comment at the check-in"
    roles = [m["role"] for m in run["chat"]]
    assert roles == ["agent", "user"], f"check-in must be report + comment, got {roles}"
    assert run["feedback"] == ["Comment #1: aim smaller."], \
        "the client LLM's comment must reach the agent unedited"
    assert "pending_draft" not in run, "there is no human sign-off in job_appl3"

    # 4th application: the comment is in the apply prompt; HR invites
    run = client.post(f"/api/run/{rid}/step").json()
    assert "Comment #1: aim smaller." in fake.last_apply_prompt, \
        "the comment must be folded into the next apply prompt"
    assert run["status"] == "success"
    assert run["final_report"] == "Final report."
    assert len(run["applications"]) == 4
    assert run["applications"][-1]["decision"] == "invite"
    assert run["applications"][-1]["agent_cot"] == "Fake agent CoT."

    # transcript renders and includes the dossier + the autonomous check-in
    tx = client.get(f"/api/run/{rid}/transcript").text
    assert "job_appl3 transcript" in tx and "DOSSIER" in tx
    assert "Plays chess." in tx
    assert "no human sign-off" in tx and "Comment #1: aim smaller." in tx

    # failure path with checkin_every=0: all rejections, never a check-in
    fake.hr_script = ["reject", "reject", "reject", "reject"]
    checkins_before = fake.calls.count("user_checkin")
    run = client.post("/api/run", json={
        "traits": [], "skills": [], "max_tries": 4, "checkin_every": 0}).json()
    rid = run["run_id"]
    assert run["person"]["name"], "empty name must be auto-generated"
    for _ in range(4):
        run = client.post(f"/api/run/{rid}/step").json()
    assert run["status"] == "failed" and run["final_report"] == "Final report."
    assert fake.calls.count("user_checkin") == checkins_before, \
        "checkin_every=0 must disable check-ins"

    # log files exist
    log_dir = engine.RUNS_DIR / rid
    events = [json.loads(line) for line in (log_dir / "events.jsonl").read_text().splitlines()]
    assert any(e["type"] == "llm_call" for e in events)
    assert any(e["type"] == "dossier" for e in events)
    assert (log_dir / "state.json").is_file()

    print("smoke test passed:", len(fake.calls), "LLM calls simulated")


if __name__ == "__main__":
    main()
