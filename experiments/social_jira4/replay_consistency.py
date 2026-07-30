from __future__ import annotations

"""Replay the consistency gate over stored candidates, to measure how its verdict depends on
whether the judge actually deliberated.

**Why this exists.** DeepSeek-V4-Pro is adaptive-thinking, and until the ``reasoning_effort``
default landed in ``llm.make_judge_caller`` the gate was left to decide per request whether to emit
a chain-of-thought. It declined on ~19% of gate calls, and the no-CoT calls did not degrade
gracefully: they either rubber-stamped (a ~70-token ``consistent: true``) or rejected on reasoning
the model conducted *inside* its own JSON string fields, after the verdict key had already been
emitted. Three of the four consistency rejections in the v3 runs came from no-CoT calls, and each
one cost a whole optimizer step.

So the question this harness answers is narrow and empirical: **re-asked N times, does a given
stored candidate still get rejected, and does asking for reasoning explicitly change that?** It is
not a judge-quality benchmark — there is no ground-truth label for "should this candidate have been
rejected". It measures *stability* and the *rate at which each individual sentence gets flagged*,
which is what you need to tell an unlucky call from a hard candidate.

**Faithfulness.** Inputs come from ``step["cons"]["rendered"]`` — the exact ``blocks`` and
``scenario_facts`` strings the gate saw at the time — and are pushed through
``LlmConsistency.check_rendered`` so the replay uses the production prompt-assembly and parsing
path. Re-deriving the facts from today's ``render``/``decoys`` code would stop being a replay the
moment either changed.

**Arms** are ``reasoning_effort`` values. ``""`` reproduces the old behaviour (no reasoning knob on
the wire at all); ``medium`` is the new default. Everything else — model, temperature, token
ceiling, retry count — is held at the production values from ``loop.py``.

Usage::

    python -m experiments.social_jira4.replay_consistency \\
        --runs cluster_qwen_r4_v3 cluster_qwen_r4_v3_warm \\
        --trials 10 --efforts "" medium \\
        --out experiments/social_jira4/outputs/_replay_cons

Writes ``calls.jsonl`` (one row per call, full verdict + usage), ``inputs.json`` (what was replayed
and each candidate's original verdict) and ``summary.json``, and prints the summary as a table.
"""

import argparse
import json
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from experiments.social_jira4 import llm as llm_mod
from experiments.social_jira4.checks_balances import LlmConsistency

# Held at loop.py's production values so a replay call differs from the original in the arm only.
_CONSISTENCY_MAX_TOKENS = 12000     # loop.py:_CONSISTENCY_MAX_TOKENS
_JUDGE_TEMPERATURE = 0.0            # llm.make_judge_caller default — judges are deterministic
_FAILURE_KINDS = ("cast", "contradicted", "substrate", "unstable")


# --------------------------------------------------------------------------- stored inputs
@dataclass
class ReplayInput:
    """One stored consistency-gate call, replayable byte-for-byte."""

    key: str                       # "<run>/step_<nnn>", the id used in every output file
    run: str
    step: int
    blocks: str                    # cons.rendered.blocks, verbatim
    facts: str                     # cons.rendered.scenario_facts, verbatim
    orig_ok: Optional[bool]        # the verdict this candidate actually got
    orig_cot: Optional[bool]       # did that original call emit a reasoning channel?
    orig_failures: List[str] = field(default_factory=list)
    orig_reasoning_tokens: Optional[int] = None


def _reasoning_tokens(usage: Dict[str, Any]) -> Optional[int]:
    det = usage.get("completion_tokens_details")
    return det.get("reasoning_tokens") if isinstance(det, dict) else None


def collect_inputs(out_root: Path, runs: Sequence[str],
                   only_no_cot: bool = False) -> List[ReplayInput]:
    """Every consistency call that actually ran, across ``runs``.

    Steps whose gate never ran (the validator or the deterministic block check rejected first) have
    no stored inputs and are skipped — there is nothing to replay."""
    found: List[ReplayInput] = []
    for run in runs:
        for path in sorted((out_root / run / "steps").glob("step_*.json")):
            try:
                step = json.loads(path.read_text())
            except (OSError, ValueError) as exc:
                print(f"  ! skipping {path}: {exc}", file=sys.stderr)
                continue
            cons = step.get("cons")
            if not isinstance(cons, dict) or not cons.get("ran"):
                continue
            rendered = cons.get("rendered") or {}
            blocks, facts = rendered.get("blocks"), rendered.get("scenario_facts")
            if not blocks or not facts:
                print(f"  ! {run}/{path.stem}: gate ran but rendered inputs missing — skipped",
                      file=sys.stderr)
                continue
            usage = cons.get("usage") or {}
            had_cot = bool((cons.get("reasoning") or "").strip())
            if only_no_cot and had_cot:
                continue
            found.append(ReplayInput(
                key=f"{run}/{path.stem}", run=run, step=int(step.get("step", -1)),
                blocks=blocks, facts=facts,
                orig_ok=cons.get("ok"), orig_cot=had_cot,
                orig_failures=list((cons.get("raw") or {}).get("failures") or []),
                orig_reasoning_tokens=_reasoning_tokens(usage),
            ))
    return found


# --------------------------------------------------------------------------- the replay
def _one_call(gate: LlmConsistency, inp: ReplayInput, effort: str, trial: int) -> Dict[str, Any]:
    """One replay call. Never raises: a failed call is recorded as a row with ``error`` set, so one
    flaky response cannot lose the other N-1 trials for that input."""
    row: Dict[str, Any] = {"key": inp.key, "run": inp.run, "step": inp.step,
                           "effort": effort, "trial": trial}
    try:
        v = gate.check_rendered(inp.blocks, inp.facts)
    except Exception as exc:                                  # noqa: BLE001 — recorded, not raised
        row.update(error=f"{type(exc).__name__}: {exc}")
        return row
    raw = v.raw or {}
    usage = v.usage or {}
    rtok = _reasoning_tokens(usage)
    row.update(
        ok=v.ok,
        cot=bool((v.reasoning or "").strip()),
        reasoning_tokens=rtok,
        completion_tokens=usage.get("completion_tokens"),
        prompt_tokens=usage.get("prompt_tokens"),
        cost=usage.get("cost"),
        parse_error=raw.get("_parse_error"),
        failures=sorted({str(f) for f in (raw.get("failures") or [])}),
        # Span-level detail is the point of the exercise: "which sentence got flagged, as what"
        # is what distinguishes a stable objection from a one-off misreading.
        findings=[{"span": str(f.get("span") or ""), "failure": str(f.get("failure") or ""),
                   "block": str(f.get("block") or ""),
                   "falsified_by": str(f.get("falsified_by") or ""),
                   "rewrite_hint": str(f.get("rewrite_hint") or "")}
                  for f in (raw.get("findings") or []) if isinstance(f, dict)],
        explanation=str(raw.get("explanation") or ""),
        reason=v.reason,
    )
    return row


def replay(inputs: Sequence[ReplayInput], efforts: Sequence[str], trials: int,
           *, model: str, provider: str, workers: int,
           on_row=None) -> List[Dict[str, Any]]:
    """Every (input x arm x trial) combination, fanned out over ``workers`` threads.

    One ``LlmConsistency`` per arm, not per call: ``_TrackingCaller`` keeps its per-call reasoning
    and usage in ``threading.local`` precisely so one instance can be shared across a pool."""
    gates = {
        effort: LlmConsistency(
            llm_mod.make_judge_caller(provider=provider, model=model,
                                      max_tokens=_CONSISTENCY_MAX_TOKENS,
                                      temperature=_JUDGE_TEMPERATURE,
                                      reasoning_effort=effort),
        )
        for effort in efforts
    }
    jobs: List[Tuple[ReplayInput, str, int]] = [
        (inp, effort, t)
        for inp in inputs for effort in efforts for t in range(trials)
    ]
    rows: List[Dict[str, Any]] = []
    lock = threading.Lock()
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for row in pool.map(lambda j: _one_call(gates[j[1]], j[0], j[1], j[2]), jobs):
            with lock:
                rows.append(row)
                done += 1
                if on_row is not None:
                    on_row(row)
                if done % 10 == 0 or done == len(jobs):
                    print(f"  … {done}/{len(jobs)} calls", flush=True)
    return rows


# --------------------------------------------------------------------------- summarising
def _rate(hits: int, n: int) -> Optional[float]:
    return round(hits / n, 4) if n else None


def summarise(inputs: Sequence[ReplayInput], rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Per (input, arm): reject rate, CoT rate, per-failure-kind rate, and per-span flag rate.

    The span-level table is the one that answers the original question. A sentence flagged in 1 of
    10 trials was a misreading; one flagged in 9 of 10 is an objection the gate actually holds, and
    the fix belongs in the candidate rather than in the judge."""
    by: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[(r["key"], r["effort"])].append(r)

    per_input: List[Dict[str, Any]] = []
    for inp in inputs:
        for effort in sorted({r["effort"] for r in rows if r["key"] == inp.key}):
            got = by[(inp.key, effort)]
            ok_rows = [r for r in got if "error" not in r]
            n = len(ok_rows)
            spans: Dict[Tuple[str, str], int] = defaultdict(int)
            for r in ok_rows:
                for f in r["findings"]:
                    spans[(f["failure"], f["span"])] += 1
            per_input.append({
                "key": inp.key, "effort": effort,
                "orig_ok": inp.orig_ok, "orig_cot": inp.orig_cot,
                "orig_failures": inp.orig_failures,
                "calls": len(got), "errors": len(got) - n,
                "reject_rate": _rate(sum(1 for r in ok_rows if r.get("ok") is False), n),
                "cot_rate": _rate(sum(1 for r in ok_rows if r.get("cot")), n),
                "parse_error_rate": _rate(sum(1 for r in ok_rows if r.get("parse_error")), n),
                "median_reasoning_tokens": _median(
                    [r["reasoning_tokens"] for r in ok_rows
                     if isinstance(r.get("reasoning_tokens"), int)]),
                "failure_rates": {
                    k: _rate(sum(1 for r in ok_rows if k in (r.get("failures") or [])), n)
                    for k in _FAILURE_KINDS
                },
                "span_rates": sorted(
                    ({"failure": kind, "span": span, "hits": c, "rate": _rate(c, n)}
                     for (kind, span), c in spans.items()),
                    key=lambda d: (-d["hits"], d["span"]),
                ),
            })

    per_arm: List[Dict[str, Any]] = []
    for effort in sorted({r["effort"] for r in rows}):
        got = [r for r in rows if r["effort"] == effort and "error" not in r]
        n = len(got)
        per_arm.append({
            "effort": effort, "calls": n,
            "errors": sum(1 for r in rows if r["effort"] == effort and "error" in r),
            "cot_rate": _rate(sum(1 for r in got if r.get("cot")), n),
            "reject_rate": _rate(sum(1 for r in got if r.get("ok") is False), n),
            "cost": round(sum(r.get("cost") or 0 for r in got), 4),
        })
    return {"per_arm": per_arm, "per_input": per_input}


def _median(xs: List[int]) -> Optional[int]:
    return sorted(xs)[len(xs) // 2] if xs else None


def print_summary(summary: Dict[str, Any]) -> None:
    print("\n=== per arm ===")
    print(f"{'effort':>10s} {'calls':>6s} {'err':>4s} {'CoT':>7s} {'reject':>7s} {'cost($)':>8s}")
    for a in summary["per_arm"]:
        print(f"{a['effort'] or '(none)':>10s} {a['calls']:6d} {a['errors']:4d} "
              f"{_pct(a['cot_rate']):>7s} {_pct(a['reject_rate']):>7s} {a['cost']:8.3f}")

    print("\n=== per input x arm ===")
    print(f"{'key':34s} {'effort':>8s} {'orig':>6s} {'CoT':>7s} {'reject':>7s}  kinds flagged")
    for p in summary["per_input"]:
        orig = ("rej" if p["orig_ok"] is False else "ok") + ("" if p["orig_cot"] else "*")
        kinds = ", ".join(f"{k} {_pct(v)}" for k, v in p["failure_rates"].items() if v)
        print(f"{p['key']:34s} {p['effort'] or '(none)':>8s} {orig:>6s} "
              f"{_pct(p['cot_rate']):>7s} {_pct(p['reject_rate']):>7s}  {kinds or '—'}")
    print("\n('*' on orig = the original call emitted no reasoning channel)")

    print("\n=== spans flagged, by input x arm (rate over trials) ===")
    for p in summary["per_input"]:
        if not p["span_rates"]:
            continue
        print(f"\n{p['key']}  effort={p['effort'] or '(none)'}")
        for s in p["span_rates"]:
            span = s["span"] if len(s["span"]) <= 96 else s["span"][:93] + "…"
            print(f"   {_pct(s['rate']):>6s} [{s['failure']}] {span}")


def _pct(x: Optional[float]) -> str:
    return "—" if x is None else f"{100 * x:.0f}%"


# --------------------------------------------------------------------------- entry point
def main(argv: Optional[Sequence[str]] = None) -> int:
    # Not ``__doc__``: the module's explanatory string sits after ``from __future__ import
    # annotations`` (the house style here), so Python does not bind it as a docstring.
    ap = argparse.ArgumentParser(
        description="Replay the consistency gate over stored candidates, with and without an "
                    "explicit reasoning_effort, to measure how stable its verdicts are.")
    ap.add_argument("--out-root", default="experiments/social_jira4/outputs",
                    help="directory holding the run dirs to read stored candidates from")
    ap.add_argument("--runs", nargs="+", required=True,
                    help="run dir names under --out-root, e.g. cluster_qwen_r4_v3")
    ap.add_argument("--out", required=True, help="directory for calls.jsonl / summary.json")
    ap.add_argument("--trials", type=int, default=10, help="replays per (input, arm)")
    ap.add_argument("--efforts", nargs="+", default=["", "medium"],
                    help="reasoning_effort arms; '' = send no reasoning knob (pre-fix behaviour)")
    ap.add_argument("--only-no-cot", action="store_true",
                    help="replay only candidates whose ORIGINAL call emitted no reasoning channel")
    ap.add_argument("--model", default=llm_mod.DEFAULT_JUDGE_MODEL)
    ap.add_argument("--provider", default=llm_mod.DEFAULT_JUDGE_PROVIDER)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would be replayed, make no model calls")
    args = ap.parse_args(argv)

    out_root = Path(args.out_root)
    inputs = collect_inputs(out_root, args.runs, only_no_cot=args.only_no_cot)
    if not inputs:
        print("no replayable consistency calls found — nothing to do", file=sys.stderr)
        return 1

    n_calls = len(inputs) * len(args.efforts) * args.trials
    print(f"inputs={len(inputs)}  arms={args.efforts}  trials={args.trials}  -> {n_calls} calls")
    print(f"  originals: {sum(1 for i in inputs if i.orig_ok is False)} rejected, "
          f"{sum(1 for i in inputs if not i.orig_cot)} with no reasoning channel")
    for i in inputs:
        print(f"    {i.key:34s} orig_ok={i.orig_ok} cot={i.orig_cot} "
              f"failures={i.orig_failures}")
    if args.dry_run:
        return 0

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "inputs.json").write_text(json.dumps(
        [{k: v for k, v in vars(i).items() if k not in ("blocks", "facts")} for i in inputs],
        indent=2))

    # Stream every row to disk as it lands: the arms are the expensive part, and a crash at call
    # 300 of 320 should not throw away the first 299.
    calls_path = out / "calls.jsonl"
    with calls_path.open("w") as fh:
        def _write(row: Dict[str, Any]) -> None:
            fh.write(json.dumps(row) + "\n")
            fh.flush()

        rows = replay(inputs, args.efforts, args.trials,
                      model=args.model, provider=args.provider,
                      workers=args.workers, on_row=_write)

    summary = summarise(inputs, rows)
    summary["config"] = {"runs": args.runs, "trials": args.trials, "efforts": args.efforts,
                         "model": args.model, "provider": args.provider,
                         "only_no_cot": args.only_no_cot,
                         "max_tokens": _CONSISTENCY_MAX_TOKENS,
                         "temperature": _JUDGE_TEMPERATURE}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print_summary(summary)
    errs = sum(1 for r in rows if "error" in r)
    print(f"\nwrote {calls_path} ({len(rows)} rows, {errs} errored) and {out / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
