"""One-off LLM generation of the static world (companies, traits, skills).

job_appl4: a much larger job market than job_appl3 — 20 companies with 2-3
open jobs each (~50 jobs total). Cached in ``world.json`` next to this file.
Run ``python world.py`` to generate it, ``python world.py --force`` to
regenerate.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

import openrouter
import prompts

WORLD_PATH = Path(__file__).resolve().parent / "world.json"
WORLD_MODEL = os.getenv("JOB_APPL_WORLD_MODEL", "openai/gpt-oss-120b")


def _validate(world: Dict[str, Any]) -> None:
    assert isinstance(world.get("traits"), list) and len(world["traits"]) >= 20
    assert isinstance(world.get("skills"), list) and len(world["skills"]) >= 15
    for s in world["skills"]:
        assert isinstance(s, dict) and s.get("name"), f"bad skill entry: {s}"
        assert isinstance(s.get("experiences"), list) and s["experiences"], \
            f"skill {s.get('name')} has no experiences"
    companies = world.get("companies")
    assert isinstance(companies, list) and len(companies) >= 16, \
        f"job_appl4 wants a large market: got {len(companies or [])} companies, need >= 16"
    for c in companies:
        # job_appl4: richer company facts so the agent quotes them instead of
        # fabricating (founded/size/hq/website/hiring_contact)
        for key in ("id", "name", "industry", "founded", "size", "hq_city",
                    "website", "products", "philosophy", "hiring_contact", "jobs"):
            assert key in c, f"company {c.get('id', '?')} missing {key}"
        assert (c["hiring_contact"] or {}).get("name"), \
            f"company {c['id']} hiring_contact has no name"
        assert c["jobs"], f"company {c['id']} has no jobs"
        for j in c["jobs"]:
            for key in ("id", "title", "location", "employment_type",
                        "salary_range", "description"):
                assert key in j, f"job missing {key} (company {c['id']})"
    n_jobs = sum(len(c["jobs"]) for c in companies)
    assert n_jobs >= 35, f"job_appl4 wants a large market: got {n_jobs} jobs, need >= 35"


def generate_world(attempts: int = 3) -> Dict[str, Any]:
    last_error: Exception = RuntimeError("no attempt made")
    for _ in range(attempts):
        content, _usage, _reasoning = openrouter.chat(
            [
                {"role": "system", "content": prompts.WORLD_GEN_SYSTEM},
                {"role": "user", "content": prompts.WORLD_GEN_USER},
            ],
            model=WORLD_MODEL,
            temperature=0.9,
            max_tokens=32768,  # 30 skills x 3-5 experiences + 20 rich companies + CoT
        )
        world = openrouter.extract_json(content)
        try:
            if world is None:
                raise RuntimeError(
                    f"World generator returned no parseable JSON:\n{content[:2000]}")
            _validate(world)
            return world
        except (AssertionError, RuntimeError) as exc:
            last_error = exc
            print(f"world generation attempt failed, retrying: {exc}")
    raise RuntimeError(f"world generation failed after {attempts} attempts: {last_error}")


def load_world(force: bool = False) -> Dict[str, Any]:
    if WORLD_PATH.is_file() and not force:
        return json.loads(WORLD_PATH.read_text())
    world = generate_world()
    WORLD_PATH.write_text(json.dumps(world, indent=2))
    return world


if __name__ == "__main__":
    force = "--force" in sys.argv
    world = load_world(force=force)
    print(
        f"world.json: {len(world['traits'])} traits, {len(world['skills'])} skills, "
        f"{len(world['companies'])} companies, "
        f"{sum(len(c['jobs']) for c in world['companies'])} jobs"
    )
