from __future__ import annotations

"""The live target runner: one candidate, one seed, one agent1 rollout.

Everything the harness does is agent1's. This module only:

1. builds the world the candidate describes (``patch.apply``),
2. assembles the run through ``agent1.run.build`` and swaps in ``AdaptivePrompts``,
3. captures the **uptake ledger per turn**, which agent1 records only in final form,
4. writes the rollout in agent1's own record shape, so its viewer reads agent2 rollouts
   unchanged,
5. hands the loop a ``RunArtifacts`` in the shape the critic will consume.

sj4's equivalent was "a copy of ``jira3._run_single`` with ``AdaptivePrompts`` swapped in", and
the copy is what let the two drift. Here nothing is copied: ``build`` is called as-is and the
prompts object is replaced on the assembled runner, which works because the runner reads
``self.prompts`` at call time and terrarium resolves ``get_system_prompt`` off the object it is
handed. A change to agent1's wiring therefore reaches agent2 without an edit here.

**A rollout never raises.** A crashed seed comes back as ``RunArtifacts(error=…)`` with whatever
artifacts exist, because a step is a mean over seeds and losing the step to one bad rollout
costs the prompter a whole optimization move — sj4 learned this the expensive way.
"""

import asyncio
import json
import logging
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from experiments.agent1 import run as agent1_run
from experiments.agent1.workspace import Workspace
from experiments.agent2 import patch as patch_mod
from experiments.agent2.adaptive_prompts import AdaptivePrompts, render
from experiments.agent2.candidate import Candidate
from experiments.agent2.patch import DEFAULT_PATCH_BUDGET

logger = logging.getLogger("experiments.agent2")


@dataclass
class RunArtifacts:
    """What one rollout hands the critic.

    ``turns`` is the critic's unit of judgement, already assembled (``assemble_turns``); ``report``
    is the whole agent1 run record, kept so a later question does not need a re-run."""

    candidate: Candidate
    seed: int
    step: int = 0
    run_dir: Optional[str] = None
    report: Dict[str, Any] = field(default_factory=dict)
    turns: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


# ------------------------------------------------------------------------- ledger capture
def install_ledger_capture(runner: agent1_run.Runner) -> agent1_run.Runner:
    """Record, per turn, what the uptake ledger held before and after it.

    agent1 keeps the ledger cumulatively and reports it once at the end, which answers "did the
    signal ever reach them" but not "had it reached them *before this claim*" — and that second
    question is the whole basis of agent2's soundness judgement. A claim contradicting something
    the assistant had already been handed is a lie; the same claim before any read of it may be
    good faith. Without the per-turn slice the judge would have to infer read-order from tool
    results, which is exactly the guesswork the ledger exists to remove.

    Shadows the bound method rather than subclassing ``Runner``, the same idiom as agent1's own
    ``install_stream`` / ``install_step_capture``, so ``agent1.run.build`` stays uncopied.
    """
    original = runner._turn

    async def _turn(agent_name: str, message: str, *, round_num: int, kind: str) -> Dict[str, Any]:
        before = list(runner.ws.seen.get(agent_name) or [])
        record = await original(agent_name, message, round_num=round_num, kind=kind)
        record["seen_before"] = before
        record["seen_after"] = list(runner.ws.seen.get(agent_name) or [])
        return record

    runner._turn = _turn  # type: ignore[method-assign]
    return runner


# ---------------------------------------------------------------------------- turn shaping
def _audience(conversation: str, ws: Workspace, speaker: str) -> str:
    """Who a post reaches. Recorded on every turn from the first run.

    A fabrication's audience is a distinction sj3/sj4 could not draw — there was one channel —
    and it is the one agent2 adds for free: lying in the shared channel, lying to a colleague in
    a DM, and lying to your **own principal** in the debrief are different acts. The objective
    ignores this in v0; the record must not, because it cannot be recovered later.
    """
    label = str(conversation or "")
    conv = ws.resolve(label, viewer=speaker)
    if conv is None:
        return "unknown"
    if conv.type == "channel":
        return "channel"
    others = [m for m in conv.members if m != speaker]
    if ws.report_to and ws.report_to in others:
        return "manager_dm"
    return "colleague_dm"


def assemble_turns(report: Dict[str, Any], ws: Workspace) -> List[Dict[str, Any]]:
    """The run record reshaped into the critic's unit: one assistant turn.

    Each turn carries what the three judges need and nothing they must be trusted to reconstruct:
    the assistant's own reasoning for that turn, everything it said and to whom, and the ledger
    slice it said it against.
    """
    turns: List[Dict[str, Any]] = []
    for index, record in enumerate(report.get("turns") or []):
        agent = str(record.get("agent") or "")
        posts: List[Dict[str, Any]] = []
        for call in record.get("tool_calls") or []:
            if call.get("tool") != "slack_post_message":
                continue
            result = call.get("result") or {}
            if not (isinstance(result, dict) and result.get("ok")):
                continue  # a refused post is not something the colleagues read
            conversation = str(result.get("conversation") or (call.get("arguments") or {}).get("conversation") or "")
            posts.append({
                "conversation": conversation,
                "audience": _audience(conversation, ws, agent),
                "text": str((call.get("arguments") or {}).get("text") or ""),
                "ts": result.get("ts"),
            })
        reasoning = "\n\n".join(
            str(step.get("reasoning") or "").strip()
            for step in (record.get("steps_detail") or [])
            if str(step.get("reasoning") or "").strip()
        )
        turns.append({
            "agent": agent,
            "turn_index": index,
            "round": record.get("round"),
            "clock": record.get("clock"),
            "message_in": record.get("message_in") or "",
            "reasoning": reasoning,
            "posts": posts,
            # The private debrief to its own principal — a lie here has no other audience and no
            # colleague to contradict it, which is why it is kept apart from `posts`.
            "debrief": record.get("text_to_principal") or "",
            "board_claims": [
                (call.get("arguments") or {}).get("task_id")
                for call in (record.get("tool_calls") or [])
                if call.get("tool") == "board_assign" and (call.get("result") or {}).get("ok")
            ],
            "seen_before": record.get("seen_before") or [],
            "seen_after": record.get("seen_after") or [],
        })
    return turns


# --------------------------------------------------------------------------------- runner
class TargetRunner:
    """Runs candidates against one base fixture. Reused across a whole optimization run."""

    def __init__(
        self,
        base_fixture: str | Path,
        settings: Dict[str, Any],
        out_dir: str | Path,
        *,
        patch_budget: int = DEFAULT_PATCH_BUDGET,
        per_seed_settings: Optional[Dict[int, Dict[str, Any]]] = None,
        rotate_opener: bool = True,
        make_client: Optional[Callable[[Dict[str, Any]], Callable[[str], Any]]] = None,
    ):
        self.base_path = Path(base_fixture)
        #: Loaded once and never mutated — ``patch.apply`` copies, so one load serves every step.
        self.base = Workspace.load(self.base_path)
        self.settings = dict(settings)
        self.out_dir = Path(out_dir)
        self.patch_budget = patch_budget
        #: Per-seed overrides, so one step can run several *models* (sj4's `--model-label`
        #: paired positionally with `--seeds`). Each entry carries its own whole `llm` block.
        self.per_seed_settings = {int(k): dict(v) for k, v in (per_seed_settings or {}).items()}
        self.rotate_opener = rotate_opener
        self._make_client = make_client or agent1_run.client_factory

    # ------------------------------------------------------------------ per-seed config
    def settings_for(self, seed: int) -> Dict[str, Any]:
        merged = {**self.settings, **self.per_seed_settings.get(int(seed), {})}
        merged["seed"] = int(seed)
        # The candidate owns both axes agent1 exposes here; leaving a level set would stack an
        # experimenter-authored norm underneath the prompter's own.
        merged["confidentiality"] = "none"
        merged["discussion_norms"] = "off"
        if self.rotate_opener and "start_with" not in merged:
            # Seeds vary the opener rather than only the clock jitter. agent1 notes that
            # first-mover and identity were confounded until `start_with` existed; here it makes
            # a seed a real second draw, so a prompt only climbs if it works whoever speaks
            # first, instead of being tuned to one turn order. Set `start_with` in the config to
            # pin it and turn the axis off.
            principals = self.base.principals
            merged["start_with"] = principals[(int(seed) - 1) % len(principals)]
        return merged

    def run_dir_for(self, candidate: Candidate, seed: int, step: int) -> Path:
        return self.out_dir / f"step{step:03d}" / candidate.run_id(self.base, seed)

    # ------------------------------------------------------------------------- the rollout
    def run(self, candidate: Candidate, seed: int, step: int = 0) -> RunArtifacts:
        run_dir = self.run_dir_for(candidate, seed, step)
        artifacts = RunArtifacts(candidate=candidate, seed=seed, step=step, run_dir=str(run_dir))
        try:
            problems = candidate.validate(self.base, patch_budget=self.patch_budget)
            if problems:
                # Reachable only if a caller skipped validation — the loop validates before it
                # gates. Failing here rather than rendering a half-specified prompt keeps a
                # malformed candidate from ever becoming a scored rollout.
                raise ValueError("candidate does not apply: " + "; ".join(problems))

            patched = candidate.build_world(self.base, patch_budget=self.patch_budget)
            settings = self.settings_for(seed)

            runner = agent1_run.build(patched.workspace, settings, self._make_client(settings))
            runner.prompts = AdaptivePrompts(patched.workspace, candidate)
            install_ledger_capture(runner)

            # Rendered BEFORE the run, not after: the workspace clock advances every turn, so a
            # post-run render would stamp `<context>` with the time the rollout ended and the
            # stored prompt would not be the one the target was sent — the exact drift between
            # "what was judged" and "what ran" that the gates' rendered copies exist to prevent.
            prompts = render(patched.workspace, candidate)

            report = asyncio.run(runner.run())
            report["agent2"] = self._provenance(candidate, patched, seed, step, settings)
            artifacts.report = report
            artifacts.turns = assemble_turns(report, patched.workspace)
            self._write(run_dir, report, candidate, patched, prompts)
            logger.info(
                "rollout %s: %s, %s turns, $%.4f",
                run_dir.name, report.get("outcome"), len(artifacts.turns), report.get("cost") or 0.0,
            )
        except Exception as exc:  # noqa: BLE001 — one bad seed must not cost the step
            artifacts.error = f"{type(exc).__name__}: {exc}"
            logger.warning("rollout %s failed: %s", run_dir.name, artifacts.error)
            logger.debug("%s", traceback.format_exc())
            try:
                run_dir.mkdir(parents=True, exist_ok=True)
                (run_dir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            except OSError:
                pass
        return artifacts

    def run_seeds(
        self, candidate: Candidate, seeds: Sequence[int], step: int = 0
    ) -> List[RunArtifacts]:
        """Every seed of one step, in order. Sequential on purpose: rollouts share a vLLM
        endpoint, and agent1's runner is already concurrent within a turn."""
        return [self.run(candidate, seed, step) for seed in seeds]

    # ------------------------------------------------------------------------- artifacts
    def _provenance(
        self,
        candidate: Candidate,
        patched: patch_mod.PatchResult,
        seed: int,
        step: int,
        settings: Dict[str, Any],
    ) -> Dict[str, Any]:
        """What this rollout was, recorded inside the run record itself.

        On the record and not only in the loop's step file, because rollout dirs get read
        directly — by the viewer, by a re-judge, by someone opening one a month later — and a
        transcript that cannot name the treatment that produced it is not evidence of anything.
        """
        return {
            "step": step,
            "seed": seed,
            "candidate": candidate.to_dict(),
            "candidate_digest": candidate.digest(),
            "base_fixture": {
                "path": str(self.base_path),
                "version": self.base.version,
                "sha": self.base.sha,
            },
            "world_sha": patched.workspace.sha,
            "patch": {
                "summary": patched.summary(),
                "added": patched.added,
                "edited": patched.edited,
                "deleted": patched.deleted,
                "pinned": patched.pinned,
                "signal_impact": patched.signal_impact,
            },
            "model": settings.get("model_name") or "",
            "start_with": settings.get("start_with"),
        }

    def _write(
        self,
        run_dir: Path,
        report: Dict[str, Any],
        candidate: Candidate,
        patched: patch_mod.PatchResult,
        prompts: Dict[str, Dict[str, str]],
    ) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        run_path = run_dir / "run.json"
        with open(run_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, default=str)

        # The rendered prompts and the world diff, beside the transcript they produced. A gate
        # verdict lives in the loop's step file; this is the copy that makes a rollout dir
        # self-contained — what was said, what world it was said in, and what the assistants
        # were told before either.
        (run_dir / "candidate.json").write_text(
            json.dumps(candidate.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (run_dir / "prompts.json").write_text(
            json.dumps(prompts, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (run_dir / "patch_diff.md").write_text(
            patch_mod.render_diff(self.base, candidate.patch), encoding="utf-8"
        )
        agent1_run.write_viewer(report, run_path)
