from __future__ import annotations

"""One candidate, one replicate, one agent1 rollout — on the frozen world.

Everything the harness does is agent1's. Unlike agent2 there is no patched world and no prompt
subclass: the candidate is a pair of ``ask_overrides`` entries, which is a setting
``agent1.run.build`` already takes, so the run is built exactly as ``python -m
experiments.agent1.run`` would build it and the record it writes is one agent1's viewer and
agent2's judges read unchanged.

**Replicates, not seeds.** ``turn_jitter`` is 0 in every agent3 config and the seed drives only
the fictional clock's jitter, so the seed is inert here: two "seeds" of one candidate are two
independent resamples of the same request at temperature 0.7, nothing more. The number is still
written into the config (and into the run id) so a record says which replicate it is, but no part
of this pretends it controls anything.

**A rollout never raises.** A crashed replicate comes back as ``RunArtifacts(error=…)`` — a
candidate's score is a mean over the replicates that ran, and losing one to a bad rollout should
not cost the whole candidate.

**A whole batch runs in one pool** (``run_batch``). A step is three candidates by ``replicates``
rollouts; running them candidate-by-candidate would make the step cost three rounds of waiting
for no reason, since the target is a hosted API with no shared endpoint to protect. All of them
go in together and the step costs the slowest single rollout.
"""

import asyncio
import json
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from experiments.agent1 import run as agent1_run
from experiments.agent1.workspace import Workspace
from experiments.agent2.target_run import assemble_turns
from experiments.agent3.candidate import Candidate

logger = logging.getLogger("experiments.agent3.target_run")


@dataclass
class RunArtifacts:
    candidate: Candidate
    replicate: int
    step: int = 0
    run_dir: Optional[str] = None
    run_path: Optional[str] = None
    report: Dict[str, Any] = field(default_factory=dict)
    turns: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def slot(self) -> int:
        return self.candidate.slot


class TargetRunner:
    def __init__(
        self,
        fixture: str | Path,
        settings: Dict[str, Any],
        out_dir: str | Path,
        *,
        make_client: Optional[Callable[[Dict[str, Any]], Callable[[str], Any]]] = None,
        parallel_rollouts: int = 9,
    ):
        self.fixture_path = Path(fixture)
        self.base = Workspace.load(self.fixture_path)
        self.settings = dict(settings)
        self.out_dir = Path(out_dir)
        self.parallel_rollouts = max(1, int(parallel_rollouts))
        self._make_client = make_client or agent1_run.client_factory

    # ------------------------------------------------------------------ per-rollout config
    def settings_for(self, candidate: Candidate, replicate: int) -> Dict[str, Any]:
        merged = dict(self.settings)
        merged["seed"] = int(replicate)
        merged["workspace"] = str(self.fixture_path)
        # The candidate owns exactly one setting. `ask` (the fixed text the others get) comes
        # from the config or the module default; the config's own ask_overrides, if any, are
        # replaced wholesale — a leftover arm override under the prompter's would be a third,
        # unrecorded treatment.
        merged["ask"] = candidate.fixed_ask
        merged["ask_overrides"] = candidate.ask_overrides()
        return merged

    def run_dir_for(self, candidate: Candidate, replicate: int, step: int) -> Path:
        return self.out_dir / f"step{step:03d}" / candidate.run_id(self.base, replicate)

    # ------------------------------------------------------------------------- the rollout
    def run(self, candidate: Candidate, replicate: int, step: int = 0) -> RunArtifacts:
        run_dir = self.run_dir_for(candidate, replicate, step)
        art = RunArtifacts(candidate=candidate, replicate=replicate, step=step,
                           run_dir=str(run_dir))
        try:
            problems = candidate.validate(self.base)
            if problems:
                raise ValueError("candidate does not apply: " + "; ".join(problems))
            settings = self.settings_for(candidate, replicate)
            # A fresh Workspace per rollout: agent1's runner mutates it (posts, claims, clock).
            ws = Workspace.load(self.fixture_path)
            runner = agent1_run.build(ws, settings, self._make_client(settings))
            report = asyncio.run(runner.run())
            report["agent3"] = {
                "step": step, "replicate": replicate,
                "tier": candidate.tier, "slot": candidate.slot,
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
            tok = report.get("tokens") or {}
            cached = int(tok.get("cached") or 0)
            prompt = int(tok.get("prompt") or 0)
            logger.info("rollout %s (%s): %s, %s turns, $%.4f, prompt cache %d%%",
                        run_dir.name, candidate.tier or "?", report.get("outcome"),
                        len(art.turns), report.get("cost") or 0.0,
                        round(100 * cached / prompt) if prompt else 0)
        except Exception as exc:  # noqa: BLE001 — one bad rollout must not cost the candidate
            art.error = f"{type(exc).__name__}: {exc}"
            logger.warning("rollout %s failed: %s", run_dir.name, art.error)
            logger.debug("%s", traceback.format_exc())
            try:
                run_dir.mkdir(parents=True, exist_ok=True)
                (run_dir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            except OSError:
                pass
        return art

    def run_batch(self, candidates: Sequence[Candidate], replicates: int,
                  step: int = 0) -> List[RunArtifacts]:
        """Every candidate x every replicate, all in flight at once."""
        jobs: List[Tuple[Candidate, int]] = [(c, r) for c in candidates
                                             for r in range(1, int(replicates) + 1)]
        if self.parallel_rollouts <= 1 or len(jobs) <= 1:
            return [self.run(c, r, step) for c, r in jobs]
        workers = min(self.parallel_rollouts, len(jobs))
        logger.info("step %d: %d rollouts (%d candidates x %d replicates), %d at a time",
                    step, len(jobs), len(candidates), replicates, workers)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(lambda j: self.run(j[0], j[1], step), jobs))

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
