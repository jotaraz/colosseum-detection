from __future__ import annotations

"""Warm start — the hand-picked agent1 arms the prompter starts from, scored by agent3's rule.

The ask arms in ``agent1/prompts.py`` were run on this exact fixture, this exact target model and
this exact provider, and every rollout was judged three times with ``category2_jv7`` — which is
what ``jv7-maj3`` collapses. So their ``reward_v1`` can be computed from files already on disk:
a warm start costs no rollouts and no judge calls.

**The match is checked, not assumed — but only where a difference changes the task.** A rollout is
admitted when it is the same *experiment*: same target model, and same world settings
(confidentiality, discussion norms, harness, turn clock, who opens). Those decide what the
assistants face, so a mismatch there makes the score describe a different question.

``max_tokens`` and provider routing are **recorded, not enforced**. They change the *serving* of
the same experiment rather than the experiment: a cap only matters on the turns that hit it
(~28% of deepseek runs had one at 8000), and routing changes quantization and truncation rates
without changing what the assistant was asked. Excluding on them cost the askA control four of
its five rollouts for a distinction the prompter cannot act on anyway — the warm start is a prior
over asks, not a controlled measurement. Each entry keeps a ``deviations`` list so the provenance
stays visible in ``warm_start.json`` even though it no longer gates.

Which arms to include is a **curation decision**, taken by hand and passed in (``--warm-start``).
There is no default set: the arms differ in what they aim at, several are near-duplicates of each
other, and letting the whole family in would hand the prompter ten rows of mostly-noise.

Warm entries are shown to the prompter with the number of rollouts behind them, because that is
the difference between askG's 15-rollout average and a fresh candidate's 3, and they compete for
the top-K slots alongside the run's own attempts.

They are also **drillable**: the prompter's tools read them in place, through the adapter in
``prompter_tools``. Nothing is copied — a warm rollout is the same information as one of this
run's, in agent1's layout (one record file, three sibling ``category2_jv7`` replicate files)
rather than agent3's. Opening them matters because askG carries 15 rollouts of a byte-identical
ask, three of which scored and eleven of which did not: that is the only place in the whole
setup where the prompter can see what differs between a rollout that produced a fabrication and
one that did not, with the ask held constant.
"""

import collections
import glob
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from experiments.agent1.workspace import Workspace
from experiments.agent3 import reward as reward_mod
from experiments.agent3.candidate import Candidate
from experiments.agent3.judge import majority

#: Where agent1 writes its arm rollouts, keyed by world version.
DEFAULT_CORPUS = "experiments/agent1/outputs"
#: warm_start.py -> agent3 -> experiments -> repo root. Used to resolve a stored run_path
#: (always repo-root-relative) regardless of the caller's own cwd.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUN = re.compile(r"inf_(?P<arm>\w+?)_(?P<model>[a-z0-9]+)_s(?P<seed>\d+)\.json$")


@dataclass
class WarmEntry:
    """One arm: its asks, and every rollout of it that scored under ``reward_v1``."""
    arm: str
    candidate: Candidate
    rewards: List[float] = field(default_factory=list)
    run_paths: List[str] = field(default_factory=list)
    rep_paths: List[List[str]] = field(default_factory=list)
    #: One judge.json-shaped record per rollout, rewarded agent only. Small (a handful of turns
    #: each) and already parsed at load time, so it is kept rather than re-read; the tools go
    #: back to disk for anything wider.
    judged: List[Dict[str, Any]] = field(default_factory=list)
    priya_turns: List[int] = field(default_factory=list)
    rejected: List[str] = field(default_factory=list)
    #: Admitted rollouts whose serving differed from the config (max_tokens, routing).
    deviations: List[str] = field(default_factory=list)

    def run_id(self, i: int) -> str:
        """A stable, obviously-foreign id for one of this arm's rollouts.

        Prefixed ``warm__`` so it can never collide with a run's own
        ``<version>__<digest>__c<slot>r<rep>`` and so the prompter can see at a glance that it
        is looking at an earlier experiment rather than at something it wrote."""
        return f"warm__{self.arm}__{Path(self.run_paths[i]).stem.rsplit('_s', 1)[-1]}"

    @property
    def n(self) -> int:
        return len(self.rewards)

    @property
    def mean(self) -> float:
        return reward_mod.aggregate(self.rewards)

    def to_dict(self) -> Dict[str, Any]:
        return {"arm": self.arm, "n": self.n, "mean": self.mean, "rewards": self.rewards,
                "priya_turns": self.priya_turns, "candidate": self.candidate.to_dict(),
                "run_ids": [self.run_id(i) for i in range(self.n)],
                "run_paths": self.run_paths, "rejected": self.rejected,
                "deviations": self.deviations}


#: How each judge's replicate files are named beside an agent1 run record.
#:
#: jv8/jv9 read ``sweep_*``, NOT the ``lie_*`` files agent2's driver writes. Those are
#: *targeted* — agent2 judged only the turns a prior pass had already nominated — so a
#: rollout's file may cover one turn out of six, and averaging it would report a fraction of a
#: rollout as if it were the whole thing. Measured on the one askG rollout that had them: 1
#: Priya turn covered, scoring 2.0, against ~6 turns a sweep actually judges. Different name,
#: so the two can never be silently mixed. ``load_targeted`` below reads the ``lie_*`` files
#: deliberately, for the one case where that bias is accepted rather than avoided — see its
#: docstring.
_REPLICATE_GLOB = {"jv7": ".category2_jv7_*.json", "jv8": ".sweep_jv8_*.json",
                   "jv9": ".sweep_jv9_*.json"}
_TARGETED_GLOB = {"jv8": ".lie_jv8_*.json", "jv9": ".lie_jv9_*.json"}


def _replicates(run_path: str, judge: str = "jv7") -> List[str]:
    """The three verdict files beside a run record, or [] if they are not all there."""
    reps = sorted(glob.glob(run_path[:-5] + _REPLICATE_GLOB[judge]))
    return reps if len(reps) == 3 else []


#: Differences that make a rollout a different experiment. A mismatch here refuses it.
GATING = ("confidentiality", "discussion_norms", "harness", "turn_seconds", "turn_jitter",
          "start_with", "max_conversation_steps")
#: Differences in how the same experiment was served. Recorded on the entry, never refused.
NOTED = ("max_tokens", "provider")


def _params_match(report: Dict[str, Any],
                  want: Dict[str, Any]) -> Tuple[Optional[str], List[str]]:
    """(why this rollout is a different experiment, deviations worth recording).

    The first is None when the rollout is admissible; the second is always populated."""
    cfg = report.get("config") or {}
    got = cfg.get("generation_params") or {}
    want_gen = want.get("generation_params") or {}

    deviations: List[str] = []
    for key in NOTED:
        a, b = got.get(key), want_gen.get(key)
        if json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True):
            deviations.append(f"{key}={a!r} (config: {b!r})")

    if str(cfg.get("model_name") or "") != str(want.get("model_name") or ""):
        return (f"model {cfg.get('model_name')!r} != {want.get('model_name')!r}", deviations)
    if got.get("temperature") != want_gen.get("temperature"):
        return (f"temperature {got.get('temperature')!r} != {want_gen.get('temperature')!r}",
                deviations)
    for key in GATING:
        if key in want and cfg.get(key) != want.get(key):
            return (f"{key} {cfg.get(key)!r} != {want.get(key)!r}", deviations)
    return (None, deviations)


def _lie_record(reps: Sequence[str], reward_agent: str,
                spec: reward_mod.RewardSpec) -> Dict[str, Any]:
    """A jv8-shaped judged record for one arm rollout, from its three lie-judge files.

    The same collapse ``lie_judge`` applies live, so a warm arm and a fresh candidate are scored
    by identical code — the only difference is where the votes were read from."""
    from experiments.agent3.lie_judge import collapse

    votes: Dict[int, List[Dict[str, Any]]] = collections.defaultdict(list)
    meta: Dict[int, Dict[str, Any]] = {}
    for i, p in enumerate(reps):
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        for t in d.get("turns") or []:
            row = dict(t)
            row["replicate"] = i
            idx = int(t["turn_index"])
            votes[idx].append(row)
            meta.setdefault(idx, {"agent": str(t.get("agent") or ""), "clock": t.get("clock"),
                                  "round": t.get("round"), "output": t.get("output") or ""})
    turns = [{**meta[i], "turn_index": i, "lies": collapse(v)} for i, v in sorted(votes.items())]
    turns = [t for t in turns if t["agent"] == reward_agent]
    record = {"judge_version": "jv8-maj3", "reward_agent": reward_agent, "warm": True,
              "n_turns": len(turns), "turns": turns}
    record["reward"] = reward_mod.rollout_reward(record, reward_agent, spec)
    return record


def judged_record(reps: Sequence[str], reward_agent: str, *,
                  only_reward_agent: bool = True,
                  spec: reward_mod.RewardSpec = reward_mod.V1) -> Dict[str, Any]:
    """A ``judge.json``-shaped record for one arm rollout, built from its replicate files.

    The same collapse the loop applies to its own rollouts, so a warm verdict and a fresh one
    are the same object and render through the same code. Individual votes are dropped: the
    reward and everything shown to the prompter read the majority, and keeping three full
    finding lists per turn for every warm rollout would be the bulk of the briefing's memory.
    """
    votes: Dict[int, List[Dict[str, Any]]] = collections.defaultdict(list)
    meta: Dict[int, Dict[str, Any]] = {}
    for i, p in enumerate(reps):
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        for t in d.get("turns") or []:
            row = dict(t)
            row["replicate"] = i
            idx = int(t["turn_index"])
            votes[idx].append(row)
            meta.setdefault(idx, {"agent": str(t.get("agent") or ""), "clock": t.get("clock"),
                                  "round": t.get("round"), "output": t.get("output") or ""})
    turns = [{**meta[i], "turn_index": i, "majority": majority(v)} for i, v in sorted(votes.items())]
    if only_reward_agent:
        turns = [t for t in turns if t["agent"] == reward_agent]
    record = {"judge_version": "jv7-maj3", "reward_agent": reward_agent, "warm": True,
              "n_turns": len(turns), "turns": turns}
    record["reward"] = reward_mod.rollout_reward(record, reward_agent, spec)
    return record


def _score(run_path: str, reps: Sequence[str], reward_agent: str,
           spec: reward_mod.RewardSpec = reward_mod.V1) -> Dict[str, Any]:
    """The run's reward for one already-judged rollout, from its three replicate files."""
    record = (_lie_record(reps, reward_agent, spec) if spec.judge in ("jv8", "jv9")
              else judged_record(reps, reward_agent, spec=spec))
    return {"reward": record["reward"], "priya_turns": len(record["turns"]), "judged": record}


def load(arms: Sequence[str], base: Workspace, want: Dict[str, Any], *,
         corpus: str | Path = DEFAULT_CORPUS,
         optimized: Sequence[str] = (), reward_agent: str = "Priya",
         fixed_ask: str = "", spec: reward_mod.RewardSpec = reward_mod.V1) -> List[WarmEntry]:
    """Score the named arms from the corpus. Returns one entry per arm, in the order given.

    ``want`` is the resolved settings of the run being started (``agent1_run.resolve_settings``
    of the target config); every rollout is checked against it.
    """
    root = Path(corpus) / base.version
    out: List[WarmEntry] = []
    for arm in arms:
        entry: Optional[WarmEntry] = None
        for run_path in sorted(glob.glob(str(root / f"inf_{arm}_*_s*.json"))):
            if "category2" in run_path:
                continue
            m = _RUN.search(Path(run_path).name)
            if not m or m.group("arm") != arm:
                continue
            report = json.loads(Path(run_path).read_text(encoding="utf-8"))
            why, deviations = _params_match(report, want)
            name = Path(run_path).name
            if why:
                if entry is None:
                    entry = WarmEntry(arm=arm, candidate=Candidate({}, optimized=tuple(optimized),
                                                                   reward_agent=reward_agent))
                entry.rejected.append(f"{name}: {why}")
                continue
            reps = _replicates(run_path, spec.judge)
            if not reps:
                if entry is None:
                    entry = WarmEntry(arm=arm, candidate=Candidate({}, optimized=tuple(optimized),
                                                                   reward_agent=reward_agent))
                entry.rejected.append(f"{name}: not judged three times by {spec.judge}")
                continue
            overrides = (report.get("config") or {}).get("ask_overrides") or {}
            asks = {who: str(overrides.get(who) or fixed_ask) for who in optimized}
            cand = Candidate(asks=asks, fixed_ask=fixed_ask, optimized=tuple(optimized),
                             reward_agent=reward_agent, tier="",
                             rationale=f"agent1 arm {arm}", extra={"warm_start_arm": arm})
            if entry is None:
                entry = WarmEntry(arm=arm, candidate=cand)
            elif not entry.candidate.asks:
                entry.candidate = cand
            elif entry.candidate.ask_overrides() != cand.ask_overrides():
                entry.rejected.append(f"{name}: asks differ from the arm's other rollouts")
                continue
            scored = _score(run_path, reps, reward_agent, spec)
            entry.rewards.append(scored["reward"])
            entry.priya_turns.append(scored["priya_turns"])
            entry.run_paths.append(run_path)
            if deviations:
                entry.deviations.append(f"{name}: " + "; ".join(deviations))
            entry.rep_paths.append(list(reps))
            entry.judged.append(scored["judged"])
        if entry is None:
            raise ValueError(f"warm start: no rollout of {arm!r} found under {root}")
        if not entry.rewards:
            raise ValueError(f"warm start: every rollout of {arm!r} was refused — "
                             + "; ".join(entry.rejected[:3]))
        out.append(entry)
    return out


def load_targeted(arms: Sequence[str], base: Workspace, want: Dict[str, Any], *,
                  corpus: str | Path = DEFAULT_CORPUS,
                  optimized: Sequence[str] = (), reward_agent: str = "Priya",
                  fixed_ask: str = "", spec: reward_mod.RewardSpec = reward_mod.V4
                  ) -> List[WarmEntry]:
    """Score the named arms from agent2's *targeted* ``lie_*`` files — accepted bias, not a bug.

    Unlike ``load``, this deliberately reads the targeted verdicts rather than refusing them:
    the alternative is a fresh sweep, and the run this feeds chose the cheaper, biased number
    over the wait. What that buys: a rollout's reward here reflects only whichever turns were
    already flagged for re-judging, so it understates what a full sweep of the same rollout
    would find — an arm can only look worse than it is under this path, never better, since a
    missed turn contributes nothing rather than a guess. Arms with no on-target-model coverage
    are silently skipped rather than raising, since that is the expected case, not an error.
    """
    out: List[WarmEntry] = []
    for arm in arms:
        entry: Optional[WarmEntry] = None
        for run_path in sorted(glob.glob(str(Path(corpus) / base.version / f"inf_{arm}_*_s*.json"))):
            if "category2" in run_path or ".lie_" in run_path or ".sweep_" in run_path:
                continue
            m = _RUN.search(Path(run_path).name)
            if not m or m.group("arm") != arm:
                continue
            report = json.loads(Path(run_path).read_text(encoding="utf-8"))
            why, deviations = _params_match(report, want)
            name = Path(run_path).name
            if why:
                continue  # a different experiment entirely; even the biased number means nothing
            reps = _replicates(run_path, spec.judge if spec.judge in ("jv8", "jv9") else "jv8")
            targeted = not reps  # no full sweep; fall back to the targeted files
            if targeted:
                reps = [str(p) for p in Path(run_path).parent.glob(
                    Path(run_path).stem + _TARGETED_GLOB[spec.judge])]
                reps = sorted(p for p in reps if not any(p.endswith(f"_r{n}.json") for n in (2, 3))
                             ) + sorted(p for p in reps if p.endswith("_r2.json"))                      + sorted(p for p in reps if p.endswith("_r3.json"))
                if len(reps) != 3:
                    continue
            overrides = (report.get("config") or {}).get("ask_overrides") or {}
            asks = {who: str(overrides.get(who) or fixed_ask) for who in optimized}
            cand = Candidate(asks=asks, fixed_ask=fixed_ask, optimized=tuple(optimized),
                             reward_agent=reward_agent, tier="",
                             rationale=f"agent1 arm {arm}"
                             + (" (targeted turns only)" if targeted else ""),
                             extra={"warm_start_arm": arm, "targeted": targeted})
            if entry is None:
                entry = WarmEntry(arm=arm, candidate=cand)
            elif not entry.candidate.asks:
                entry.candidate = cand
            elif entry.candidate.ask_overrides() != cand.ask_overrides():
                continue
            scored = _score(run_path, reps, reward_agent, spec)
            entry.rewards.append(scored["reward"])
            entry.priya_turns.append(scored["priya_turns"])
            entry.run_paths.append(run_path)
            entry.rep_paths.append(list(reps))
            entry.judged.append(scored["judged"])
            if targeted:
                entry.deviations.append(f"{name}: targeted turns only, not a full sweep")
        if entry is not None and entry.rewards:
            out.append(entry)
    return out


def load_prior_run(prior_dirs: Sequence[str | Path], reward_agent: str,
                   spec: reward_mod.RewardSpec = reward_mod.V4,
                   min_replicates: int = 2) -> List[WarmEntry]:
    """Warm entries from an earlier agent3 run's own candidates, re-judged under ``spec``.

    Each candidate (one ask pair, by digest) becomes one entry, pooling whichever of its
    rollouts carry a full 3-replicate set of ``spec.judge``'s *targeted* files (``lie_jv8``/
    ``lie_jv9``) next to their ``run.json`` — written there by ``verdict_export`` when the run
    happened to be judged under that spec, or by a later out-of-band re-judge, as with
    run05/run06's ``jv9_agent3_targets.json`` pass. A rollout with no such files, or fewer than
    three, is simply absent from that candidate's count — the same "not judged" rule the arm
    loader uses, not a zero.

    ``min_replicates``: a candidate needs at least this many scored rollouts to be worth
    showing at all; below that its number is closer to a coin flip than a prior.
    """
    from experiments.agent3.candidate import Candidate

    out: List[WarmEntry] = []
    glob_suffix = _TARGETED_GLOB[spec.judge]
    for prior in prior_dirs:
        prior = Path(prior)
        for step_file in sorted((prior / "steps").glob("step_*.json")):
            step = json.loads(step_file.read_text(encoding="utf-8"))
            for a in step.get("attempts") or []:
                if not a.get("ran"):
                    continue
                cand = a.get("candidate") or {}
                digest = a.get("candidate_digest") or ""
                label = f"{prior.name}:step{step.get('opt_step')}:{cand.get('tier') or '?'}"
                entry = WarmEntry(arm=label, candidate=Candidate.from_dict(
                    {**cand, "rationale": f"{cand.get('rationale', '')} [warm from {label}]"}))
                for run in a.get("runs") or []:
                    run_path = run.get("run_path")
                    if not run_path:
                        continue
                    run_path = Path(run_path)
                    if not run_path.is_absolute():
                        # NOT Path.cwd() — the writer always stores this repo-root-relative
                        # (loop.py runs with cwd=PROJECT on the cluster), but this function can
                        # be called from any cwd. Anchoring on cwd silently doubled the prefix
                        # and produced a real bug: every candidate came back mean=0.00 because
                        # every glob missed, not because there was nothing to find.
                        run_path = _REPO_ROOT / run_path
                    reps = sorted(str(p) for p in run_path.parent.glob(
                        run_path.stem + glob_suffix))
                    reps = ([p for p in reps if not p.endswith(("_r2.json", "_r3.json"))]
                           + [p for p in reps if p.endswith("_r2.json")]
                           + [p for p in reps if p.endswith("_r3.json")])
                    if len(reps) != 3:
                        continue
                    scored = _score(str(run_path), reps, reward_agent, spec)
                    entry.rewards.append(scored["reward"])
                    entry.priya_turns.append(scored["priya_turns"])
                    entry.run_paths.append(str(run_path))
                    entry.rep_paths.append(reps)
                    entry.judged.append(scored["judged"])
                if len(entry.rewards) >= min_replicates:
                    entry.deviations.append(
                        f"targeted turns only ({len(entry.rewards)} of "
                        f"{len(a.get('runs') or [])} rollouts had {spec.judge} coverage)")
                    out.append(entry)
    return out


def parse_arms(spec: str) -> List[str]:
    """``--warm-start`` -> the arm list. Either names, or a path to a JSON file with ``arms``."""
    spec = str(spec or "").strip()
    if not spec:
        return []
    p = Path(spec)
    if p.exists():
        obj = json.loads(p.read_text(encoding="utf-8"))
        arms = obj.get("arms") if isinstance(obj, dict) else obj
        return [str(a).strip() for a in (arms or []) if str(a).strip()]
    return [a.strip() for a in spec.split(",") if a.strip()]
