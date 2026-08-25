from __future__ import annotations

"""One candidate, one seed, one agent1 rollout — on the frozen v17 world.

Everything the harness does is agent1's. Unlike agent2 there is no patched world and no prompt
subclass: the candidate is two ``ask_overrides`` entries, which is a setting ``agent1.run.build``
already takes, so the run is built exactly as ``python -m experiments.agent1.run`` would build it
and the record it writes is one agent1's viewer and agent2's judges read unchanged.

**A rollout never raises.** A crashed seed comes back as ``RunArtifacts(error=…)`` — a step is a
mean over seeds, and losing the step to one bad rollout costs the prompter a whole move.

Seeds of one step run **concurrently** (``--parallel-seeds``): the target is a hosted API, so
there is no shared endpoint to protect, and a glm-5.2 rollout is 15–25 minutes of mostly waiting.
Each seed gets its own thread and its own event loop.
"""

import asyncio
import json
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from experiments.agent1 import run as agent1_run
from experiments.agent1.workspace import Workspace
from experiments.agent2.target_run import assemble_turns
from experiments.agent3.candidate import Candidate

logger = logging.getLogger("experiments.agent3.target_run")


@dataclass
class RunArtifacts:
    candidate: Candidate
    seed: int
    step: int = 0
    run_dir: Optional[str] = None
    run_path: Optional[str] = None
    report: Dict[str, Any] = field(default_factory=dict)
    turns: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


class TargetRunner:
    def __init__(
        self,
        fixture: str | Path,
        settings: Dict[str, Any],
        out_dir: str | Path,
        *,
        make_client: Optional[Callable[[Dict[str, Any]], Callable[[str], Any]]] = None,
        parallel_seeds: int = 3,
    ):
        self.fixture_path = Path(fixture)
        self.base = Workspace.load(self.fixture_path)
        self.settings = dict(settings)
        self.out_dir = Path(out_dir)
        self.parallel_seeds = max(1, int(parallel_seeds))
        self._make_client = make_client or agent1_run.client_factory

    # ------------------------------------------------------------------ per-seed config
    def settings_for(self, candidate: Candidate, seed: int) -> Dict[str, Any]:
        merged = dict(self.settings)
        merged["seed"] = int(seed)
        merged["workspace"] = str(self.fixture_path)
        # The candidate owns exactly one setting. `ask` (the fixed text the other two get) comes
        # from the config or the module default; the config's own ask_overrides, if any, are
        # replaced wholesale — a leftover arm override under the prompter's would be a third,
        # unrecorded treatment.
        merged["ask"] = candidate.fixed_ask
        merged["ask_overrides"] = candidate.ask_overrides()
        return merged

    def run_dir_for(self, candidate: Candidate, seed: int, step: int) -> Path:
        return self.out_dir / f"step{step:03d}" / candidate.run_id(self.base, seed)

    # ------------------------------------------------------------------------- the rollout
    def run(self, candidate: Candidate, seed: int, step: int = 0) -> RunArtifacts:
        run_dir = self.run_dir_for(candidate, seed, step)
        art = RunArtifacts(candidate=candidate, seed=seed, step=step, run_dir=str(run_dir))
        try:
            problems = candidate.validate(self.base)
            if problems:
                raise ValueError("candidate does not apply: " + "; ".join(problems))
            settings = self.settings_for(candidate, seed)
            # A fresh Workspace per rollout: agent1's runner mutates it (posts, claims, clock).
            ws = Workspace.load(self.fixture_path)
            runner = agent1_run.build(ws, settings, self._make_client(settings))
            report = asyncio.run(runner.run())
            report["agent3"] = {
                "step": step, "seed": seed,
                "candidate": candidate.to_dict(),
                "candidate_digest": candidate.digest(),
                "fixture": {"path": str(self.fixture_path), "version": self.base.version,
                            "sha": self.base.sha},
                "model": settings.get("model_name") or "",
                "start_with": settings.get("start_with"),
            }
            art.report = report
            art.turns = assemble_turns(report, ws)
            art.run_path = str(self._write(run_dir, report, candidate))
            logger.info("rollout %s: %s, %s turns, $%.4f", run_dir.name, report.get("outcome"),
                        len(art.turns), report.get("cost") or 0.0)
        except Exception as exc:  # noqa: BLE001 — one bad seed must not cost the step
            art.error = f"{type(exc).__name__}: {exc}"
            logger.warning("rollout %s failed: %s", run_dir.name, art.error)
            logger.debug("%s", traceback.format_exc())
            try:
                run_dir.mkdir(parents=True, exist_ok=True)
                (run_dir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            except OSError:
                pass
        return art

    def run_seeds(self, candidate: Candidate, seeds: Sequence[int], step: int = 0) -> List[RunArtifacts]:
        seeds = list(seeds)
        if self.parallel_seeds <= 1 or len(seeds) <= 1:
            return [self.run(candidate, s, step) for s in seeds]
        with ThreadPoolExecutor(max_workers=min(self.parallel_seeds, len(seeds))) as pool:
            return list(pool.map(lambda s: self.run(candidate, s, step), seeds))

    # ------------------------------------------------------------------------- artifacts
    def _write(self, run_dir: Path, report: Dict[str, Any], candidate: Candidate) -> Path:
        run_dir.mkdir(parents=True, exist_ok=True)
        run_path = run_dir / "run.json"
        with open(run_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, default=str)
        (run_dir / "candidate.json").write_text(
            json.dumps(candidate.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        (run_dir / "asks.json").write_text(
            json.dumps(candidate.all_asks(self.base), indent=2, ensure_ascii=False), encoding="utf-8")
        agent1_run.write_viewer(report, run_path)
        return run_path
