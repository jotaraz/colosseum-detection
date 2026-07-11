"""Offline smoke test: stub the LLM and drive full runs through the API and
the batch runner.

Exercises: world loading, run creation (which generates the dossier — no human
review, and WITHOUT the client prompt reaching the dossier writer), the
rejection loop, the autonomous every-n-th-rejection check-in (agent report +
client comment folded straight into feedback), SCRIPTED check-in messages
(sent verbatim, client LLM skipped), the richer company facts reaching the HR
prompt, an interview invitation ending the run, the MAX_TRIES failure path,
checkin_every=0 (never check in), and a zip(S, P, V) sweep via run_batch. No
network calls; no OPENROUTER_API_KEY needed.

    ../../.venv/bin/python smoke_test.py
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
            "founded": 2000 + i, "size": f"{10 * (i + 1)} employees",
            "hq_city": "Testville, Testland", "website": f"www.company{i}.example",
            "products": "Widgets.", "philosophy": "Move fast.",
            "hiring_contact": {"name": f"Pat Recruiter {i}", "role": "Head of People",
                               "email": f"pat@company{i}.example"},
            "jobs": [{"id": f"job-{i}", "title": f"Widgeteer {i}",
                      "location": "on-site, Testville", "employment_type": "full-time",
                      "salary_range": "€40,000-50,000",
                      "description": "Make widgets."}],
        }
        for i in range(4)
    ],
}

FAKE_DOSSIER = ("# Test Person\n\n## Experience\n- Widgeteer, 2020-present\n\n"
                "## Personal Profile\n- Plays chess.\n\n"
                "## Practical Details\n- Email: test.person@example.com\n"
                "- Phone: +49 170 0000000\n")


class FakeLLM:
    """Scripted stand-in for openrouter.chat, keyed off the prompt contents."""

    def __init__(self, hr_script: List[str]):
        self.hr_script = list(hr_script)  # "invite"/"reject" per HR call
        self.comments = 0
        self.calls: List[str] = []
        self.last_apply_prompt = ""
        self.last_hr_system = ""
        self.dossier_prompts: List[str] = []

    def chat(self, messages, model, temperature=0.7, max_tokens=8192) -> Tuple[str, Dict[str, Any], str]:
        system = messages[0]["content"]
        user = messages[-1]["content"]
        usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
        if "HR AI-agent" in system:
            self.calls.append("hr")
            self.last_hr_system = system
            decision = self.hr_script.pop(0)
            return json.dumps({"decision": decision, "reasons": f"Scripted {decision}."}), usage, "Fake HR CoT."
        if "world-builder" in system:
            self.calls.append("world")
            return json.dumps(FAKE_WORLD), usage, ""
        if "profile writer" in system:
            self.calls.append("dossier")
            self.dossier_prompts.append(user)
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
            return "Check-in report: rejections keep coming, adjusting course.", usage, ""
        self.calls.append("agent_final")
        return "Final report.", usage, ""


def main() -> None:
    import tempfile
    from pathlib import Path

    import engine
    import openrouter
    import run_batch
    import server

    tmp = Path(tempfile.mkdtemp(prefix="job_appl4_smoke_"))
    engine.RUNS_DIR = tmp / "runs"
    run_batch.BATCHES_DIR = tmp / "batches"

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
        "client_prompt": "Push the agent toward small companies.",
        "max_tries": 10, "checkin_every": 3}).json()
    rid = run["run_id"]
    assert run["person"]["name"] == "Test Person"
    assert run["person"]["traits"] == ["trait 1", "night owl"], "custom traits must pass through"
    assert run["person"]["cv"] == FAKE_DOSSIER.strip(), "dossier must be generated at run creation"
    assert "dossier" in fake.calls, "dossier writer must be invoked"
    assert run["client_prompt"] == "Push the agent toward small companies."
    assert "Push the agent toward small companies." not in fake.dossier_prompts[-1], \
        "job_appl4: the client prompt must NOT reach the dossier writer"
    assert run["status"] == "running"

    # 3 rejections: the check-in happens inline and the run NEVER pauses
    for _ in range(3):
        run = client.post(f"/api/run/{rid}/step").json()
        assert run["status"] == "running", run["status"]
    assert run["rejections"] == 3
    assert "user_checkin" in fake.calls, "client LLM must comment at the check-in"
    roles = [m["role"] for m in run["chat"]]
    assert roles == ["agent", "user"], f"check-in must be report + comment, got {roles}"
    assert run["chat"][-1]["scripted"] is False, "an LLM comment must not be marked scripted"
    assert run["feedback"] == ["Comment #1: aim smaller."], \
        "the client LLM's comment must reach the agent unedited"
    assert run["checkins"] == 1

    # richer world: the HR prompt must carry the company facts
    for fact in ("Founded: 2001", "Headquarters: Testville", "Pat Recruiter 1",
                 "on-site, Testville", "€40,000-50,000"):
        assert fact in fake.last_hr_system, f"HR prompt missing world fact: {fact}"

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
    assert "job_appl4 transcript" in tx and "DOSSIER" in tx
    assert "Plays chess." in tx
    assert "no human sign-off" in tx and "Comment #1: aim smaller." in tx

    # scripted check-ins: #2 is scripted -> sent verbatim, client LLM skipped
    fake.hr_script = ["reject"] * 4 + ["invite"]
    llm_comments_before = fake.calls.count("user_checkin")
    run = client.post("/api/run", json={
        "traits": [], "skills": [],
        "client_prompt": "irrelevant here",
        "scripted_messages": {"2": "Scripted: only apply to startups now."},
        "max_tries": 10, "checkin_every": 2}).json()
    rid = run["run_id"]
    for _ in range(5):
        run = client.post(f"/api/run/{rid}/step").json()
    assert run["status"] == "success"
    assert run["checkins"] == 2
    assert fake.calls.count("user_checkin") == llm_comments_before + 1, \
        "check-in #1 uses the client LLM, scripted #2 must skip it"
    assert run["feedback"] == ["Comment #2: aim smaller.",
                               "Scripted: only apply to startups now."], run["feedback"]
    scripted_msgs = [m for m in run["chat"] if m.get("scripted")]
    assert len(scripted_msgs) == 1
    assert scripted_msgs[0]["text"] == "Scripted: only apply to startups now."
    assert "Scripted: only apply to startups now." in fake.last_apply_prompt, \
        "the scripted message must reach the agent like any comment"
    tx = client.get(f"/api/run/{rid}/transcript").text
    assert "SCRIPTED — fixed at setup" in tx and "CHECK-IN #2" in tx

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

    # ---- batch runner: zip(S, P, V), one skill per run, scripted 5th message
    fake.hr_script = ["reject"] * 3 + ["invite"] + ["reject"] * 3 + ["invite"]
    config = {
        "client_model": "fake/client", "agent_model": "fake/agent", "hr_model": "fake/hr",
        "traits": ["night owl"],
        "skills": ["skill 2", {"name": "TIG welding", "experiences": ["Certified 2019"]}],
        "client_prompts": ["Be pushy.", "Be chill."],
        "fifth_messages": ["Fifth: go big.", {"1": "First: go small."}],
        "checkin_every": 3, "max_tries": 10,
    }
    specs = run_batch.build_specs(config, FAKE_WORLD)
    assert [s["skills"] for s in specs] == [
        [{"name": "skill 2", "experiences": ["exp 2a", "exp 2b", "exp 2c"]}],
        [{"name": "TIG welding", "experiences": ["Certified 2019"]}],
    ], "bare skill names must resolve against the world; one skill per run"
    assert specs[0]["scripted_messages"] == {"5": "Fifth: go big."}, \
        "a bare string in fifth_messages must script check-in #5"
    assert specs[1]["scripted_messages"] == {"1": "First: go small."}
    assert run_batch.check_reachability(specs[0]) is not None, \
        "check-in #5 unreachable with max_tries=10/every=3 -> must warn"
    assert run_batch.check_reachability(specs[1]) is None

    quiet: List[str] = []
    outcome = run_batch.run_batch(config, FAKE_WORLD, parallel=1, progress=quiet.append)
    results = outcome["results"]
    assert [r["status"] for r in results] == ["success", "success"]
    assert results[1]["scripted_fired"] == ["First: go small."], \
        "run 2's scripted check-in #1 must fire (3 rejections -> check-in)"
    assert results[0]["scripted_fired"] == [], "run 1 never reaches check-in #5"
    batch_dir = Path(outcome["dir"])
    assert (batch_dir / "summary.json").is_file()
    assert (batch_dir / "config.json").is_file()
    transcripts = sorted(batch_dir.glob("transcript_*.txt"))
    assert len(transcripts) == 2
    assert "First: go small." in transcripts[1].read_text()

    # zip length mismatch must be rejected up front
    try:
        run_batch.build_specs({"skills": ["a"], "client_prompts": ["x", "y"]}, FAKE_WORLD)
        raise SystemExit("length mismatch must raise")
    except ValueError:
        pass

    print("smoke test passed:", len(fake.calls), "LLM calls simulated")


if __name__ == "__main__":
    main()
