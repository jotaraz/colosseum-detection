from __future__ import annotations

"""Offline meta-judge sweep over the 52 qualifying v4 prompts
(``reports/v4_dspro_fabrication_qualifying.jsonl``).

Five questions x four judge models x 52 prompt-pairs = 1040 calls. The questions are the
**rationale-first** copies (``META_JUDGE_*_RATIONALE_FIRST.md``), which ask for the same JSON
envelope with ``rationale`` moved ahead of ``answer``/``confidence`` so the judge reasons before it
commits. That is a different instrument from the in-loop gate — see the header of any of those
files — so nothing collected here pools with ``reports/v3_meta_judge_verdicts.jsonl`` or with the
``meta`` block of a step file.

**What is judged.** Not a local re-render: each row's ``step_path`` is opened and the pair the gate
actually read (``meta.rendered.system`` / ``.user``) is used verbatim, in the ``system_user`` view.
Every one of the 52 re-hashes to the ``prompt_sha`` the gate recorded, and this script re-checks
that on every task rather than trusting it — a mismatch is a hard error, because a silently
re-rendered prompt would make every verdict an answer to a different question than the one asked.

**Two files, on purpose.** Verdicts land in an append-only sidecar
(``reports/v4_dspro_meta_judge_verdicts.jsonl``) as they arrive, one row per (prompt, question,
seat), so an interrupted run keeps everything it paid for; ``--merge`` then folds them into the
52-row file under ``meta_judges``. Reruns skip rows that already parsed, so a second pass only
retries failures. The sidecar keeps the judge's chain-of-thought and raw reply; the merged file
keeps the verdict only, or the 52 rows would be mostly transcript.

Usage (from the repo root):
    python -m experiments.social_jira4.meta_judge_sweep --dry-run --limit 1
    python -m experiments.social_jira4.meta_judge_sweep
    python -m experiments.social_jira4.meta_judge_sweep --seats gpt54     # retry one seat
    python -m experiments.social_jira4.meta_judge_sweep --merge
"""

import argparse
import hashlib
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from experiments.social_jira3.judge import load_prompt_template
from experiments.social_jira4.checks_balances import _call_json
from experiments.social_jira4.judge_prompts import fill, judged_text
from experiments.social_jira4.llm import Caller, make_judge_caller

HERE = Path(__file__).resolve().parent
DEFAULT_INPUT = HERE / "reports" / "v4_dspro_fabrication_qualifying.jsonl"
DEFAULT_SIDECAR = HERE / "reports" / "v4_dspro_meta_judge_verdicts.jsonl"

# The view the in-loop gate used, and the only one these rows carry a sha for.
VIEW = "system_user"


# --------------------------------------------------------------------------------- questions
# Judge 6 (COLDREAD) is deliberately absent: its envelope is free text by design, so it does not
# share the answer/confidence shape the rest of this script reads, and it was excluded from the
# request that produced this sweep.
QUESTIONS: List[Tuple[str, Path]] = [
    ("realism", HERE / "META_JUDGE_1_REALISM_RATIONALE_FIRST.md"),
    ("fabrication", HERE / "META_JUDGE_2_FABRICATION_RATIONALE_FIRST.md"),
    ("admissibility", HERE / "META_JUDGE_3_ADMISSIBILITY_RATIONALE_FIRST.md"),
    ("forecast", HERE / "META_JUDGE_4_FORECAST_RATIONALE_FIRST.md"),
    ("intent", HERE / "META_JUDGE_5_INTENT_RATIONALE_FIRST.md"),
]

# ``forecast`` asks for a probability rather than a confidence — a real distinction (a forecast of
# the target's behaviour vs. a confidence in a verdict about the prompt), so the key is not
# normalised away. Readers take whichever is present; ``confidence_field`` records which it was.
CONF_KEYS = ("confidence", "probability")


# --------------------------------------------------------------------------------- seats
@dataclass(frozen=True)
class Seat:
    """One judge model. ``pin`` is OpenRouter provider routing, forwarded verbatim.

    Pinning is not a preference: an unpinned OpenRouter call picks an upstream per request, and
    which upstream it picks changes what comes back (see ``openrouter_client.generate_response``).
    ``allow_fallbacks: false`` makes an unroutable pin fail as a loud 404 rather than quietly
    running somewhere else — the whole point of pinning, at the cost that a provider outage kills
    the seat instead of degrading it.
    """

    label: str
    provider: str
    model: str
    pin: Optional[Dict[str, Any]] = None
    temperature: Optional[float] = 0.0
    reasoning_effort: str = "medium"
    max_workers: int = 6


def _only(provider_slug: str) -> Dict[str, Any]:
    return {"only": [provider_slug], "allow_fallbacks": False}


PANEL: List[Seat] = [
    # The prompter and the in-loop gate for all 52 of these rows. Pinned to StreamLake, which does
    # support temperature and reasoning_effort — but serves fp8, and the in-loop gate ran UNPINNED,
    # so this is a re-ask on a possibly different serving stack, not a replay of that gate.
    Seat("dspro", "openrouter", "deepseek/deepseek-v4-pro", _only("streamlake")),
    # Confounded on purpose and knowingly: deepseek-v4-flash also appears as a target/assistant
    # model elsewhere in this project, so it is judging a genre of prompt its own family has been
    # on the receiving end of. Read its verdicts with that in mind. DeepInfra serves it at fp4.
    Seat("dsflash", "openrouter", "deepseek/deepseek-v4-flash-0731", _only("deepinfra")),
    # Claude Sonnet 5 REMOVED the sampling parameters: any non-default temperature is a 400, and
    # OpenRouter's endpoint metadata for this model lists no ``temperature`` at all. So the seat
    # sends none, and "temperature 0 determinism" is simply unavailable here — its depth knob is
    # reasoning_effort, which maps to Anthropic's own effort parameter (thinking is always on).
    Seat("sonnet5", "openrouter", "anthropic/claude-sonnet-5", _only("anthropic"),
         temperature=None),
    # Azure has one upstream by construction, so there is nothing to pin; its caller sends no
    # temperature and has no effort knob, which is why the four seats are NOT matched on effort.
    # Deployment name from the same env var meta_gate/judge_prompts read, so all three panels name
    # the same deployment on a cluster that overrides it.
    Seat("gpt54", "azure", os.getenv("AZURE_JUDGE_DEPLOYMENT", "gpt-5.4"), None,
         temperature=None, reasoning_effort="",
         max_workers=2),   # low: the shared gpt-5.4 deployment 429s under concurrency
]

# Judged prompts run ~2.4k tokens in and the verdicts are short, but reasoning tokens count against
# the completion budget on every seat that thinks — 4096 (the judge default) is enough for the
# DeepSeek pair and tight for a thinking Sonnet, whose truncation shows up as an EMPTY reply that
# ``_call_json`` can only retry, not rescue. 8192 costs nothing when unused.
MAX_TOKENS = 8192
RETRIES = 2


# --------------------------------------------------------------------------------- io
def load_rows(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


Key = Tuple[Any, Any, str, str]   # (run_dir, step, question, seat)


def _key(row: Dict[str, Any]) -> Key:
    return (row.get("run_dir"), row.get("step"), row.get("question"), row.get("seat"))


def done_keys(path: Path) -> set:
    """Keys already judged **successfully**. Failures are left out so a rerun retries them; the
    file is append-only and last-wins on read, so a retry's row supersedes the failure."""
    if not path.exists():
        return set()
    done = set()
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue      # truncated tail from an interrupted run — it will just be redone
            if r.get("ok"):
                done.add(_key(r))
            else:
                done.discard(_key(r))
    return done


class _Writer:
    """Append-only JSONL, flushed per row so an interrupted run keeps everything it paid for."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8")
        self._lock = threading.Lock()

    def write(self, row: Dict[str, Any]) -> None:
        with self._lock:
            self._fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._fh.flush()

    def close(self) -> None:
        self._fh.close()


# --------------------------------------------------------------------------------- prompts
def rendered_pair(row: Dict[str, Any], *, root: Path) -> Tuple[str, str, str]:
    """The (system, user, sha) the in-loop gate read, straight out of the step file.

    The sha is recomputed and checked against the row's ``prompt_sha`` rather than assumed: these
    verdicts are only comparable to the gate's if the judged bytes are the same bytes, and a step
    file that moved or a row that drifted would otherwise pass silently."""
    step_path = Path(row["step_path"])
    if not step_path.is_absolute():
        step_path = root / step_path
    step = json.loads(step_path.read_text(encoding="utf-8"))
    rendered = (step.get("meta") or {}).get("rendered") or {}
    system, user = rendered.get("system") or "", rendered.get("user") or ""
    if not system or not user:
        raise ValueError(f"{step_path}: meta.rendered is missing system/user")
    sha = hashlib.sha256(judged_text(system, user, VIEW).encode("utf-8")).hexdigest()[:16]
    if row.get("prompt_sha") and sha != row["prompt_sha"]:
        raise ValueError(
            f"{step_path}: judged text hashes to {sha}, but the row recorded "
            f"{row['prompt_sha']} — the prompt is not the one the gate judged"
        )
    return system, user, sha


# --------------------------------------------------------------------------------- judging
@dataclass
class _Task:
    row: Dict[str, Any]
    question: str
    seat: Seat
    system: str
    user: str
    sha: str


@dataclass
class _Panel:
    """A seat's caller plus the concurrency gate that keeps it inside its own rate limit."""

    seat: Seat
    caller: Caller
    sem: threading.Semaphore


def _as_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _verdict_row(task: _Task, obj: Dict[str, Any]) -> Dict[str, Any]:
    """One sidecar row: join keys, what was judged, the verdict, and its trail.

    Verdict fields are emitted rationale -> answer -> confidence, matching the order the
    rationale-first prompts ask for, so a row reads the way the judge wrote it."""
    obj = dict(obj)
    meta = obj.pop("_meta", {})
    parse_error = obj.pop("_parse_error", None)
    conf_field = next((k for k in CONF_KEYS if k in obj), None)
    out: Dict[str, Any] = {
        "run_dir": task.row.get("run_dir"),
        "step": task.row.get("step"),
        "step_path": task.row.get("step_path"),
        "question": task.question,
        "seat": task.seat.label,
        "rationale": str(obj.get("rationale") or ""),
        "answer": str(obj.get("answer", "")).strip().lower(),
        "confidence": _as_float(obj.get(conf_field)) if conf_field else None,
        "confidence_field": conf_field,
        "ok": parse_error is None,
        "model": task.seat.model,
        "provider": task.seat.provider,
        "provider_pin": task.seat.pin,
        "temperature": task.seat.temperature,
        "reasoning_effort": task.seat.reasoning_effort or None,
        "view": VIEW,
        "prompt_sha": task.sha,
        "judged_chars": len(judged_text(task.system, task.user, VIEW)),
    }
    if parse_error is not None:
        out["parse_error"] = parse_error
    out["verdict"] = obj                        # the judge's whole JSON reply, unedited
    out["reasoning"] = meta.get("reasoning", "")   # its CoT — sidecar only, never merged
    out["usage"] = meta.get("usage", {})
    out["attempts"] = meta.get("attempts")
    out["raw"] = meta.get("raw", "")
    return out


def run_task(panel: _Panel, body: str, task: _Task) -> Dict[str, Any]:
    filled = fill(body, task.system, task.user, VIEW)
    with panel.sem:
        obj = _call_json(panel.caller, filled, retries=RETRIES)
    return _verdict_row(task, obj)


# --------------------------------------------------------------------------------- merge
def merge(input_path: Path, sidecar: Path, *, log: Callable[[str], None]) -> int:
    """Fold the sidecar's verdicts into the 52-row file under ``meta_judges``.

    Last-wins per (row, question, seat), successes only — a failed call leaves the slot absent
    rather than writing a null verdict that later reads as "the judge declined"."""
    rows = load_rows(input_path)
    verdicts: Dict[Key, Dict[str, Any]] = {}
    if sidecar.exists():
        for line in sidecar.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                v = json.loads(line)
            except Exception:
                continue
            if v.get("ok"):
                verdicts[_key(v)] = v
    n_attached = 0
    for row in rows:
        block: Dict[str, Dict[str, Any]] = {}
        for qname, _ in QUESTIONS:
            per_seat: Dict[str, Any] = {}
            for seat in PANEL:
                v = verdicts.get((row.get("run_dir"), row.get("step"), qname, seat.label))
                if v is None:
                    continue
                per_seat[seat.label] = {
                    "rationale": v["rationale"],
                    "answer": v["answer"],
                    "confidence": v["confidence"],
                    "confidence_field": v["confidence_field"],
                    "model": v["model"],
                    "provider": v["provider"],
                    "provider_pin": v["provider_pin"],
                    "usage_cost_usd": (v.get("usage") or {}).get("cost_usd"),
                }
                n_attached += 1
            if per_seat:
                block[qname] = per_seat
        row["meta_judges"] = block
        row["meta_judges_prompts"] = {q: p.name for q, p in QUESTIONS}
    with input_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    expected = len(rows) * len(QUESTIONS) * len(PANEL)
    log(f"merged {n_attached}/{expected} verdicts into {input_path}")
    if n_attached < expected:
        log(f"  {expected - n_attached} slot(s) left empty — rerun the sweep to fill them")
    return 0


# --------------------------------------------------------------------------------- cli
def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Judge the 52 qualifying v4 prompts with the rationale-first meta-judge panel."
    )
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--sidecar", type=Path, default=DEFAULT_SIDECAR)
    ap.add_argument("--root", type=Path, default=HERE,
                    help="what relative step_path values resolve against (default: this dir)")
    ap.add_argument("--questions", default="",
                    help=f"comma-separated subset of {','.join(q for q, _ in QUESTIONS)}")
    ap.add_argument("--seats", default="",
                    help=f"comma-separated subset of {','.join(s.label for s in PANEL)}")
    ap.add_argument("--limit", type=int, default=0, help="only the first N prompts (smoke test)")
    ap.add_argument("--overwrite", action="store_true",
                    help="re-judge everything instead of resuming past what already parsed")
    ap.add_argument("--dry-run", action="store_true",
                    help="build and print the filled judge prompts; make no API calls")
    ap.add_argument("--merge", action="store_true",
                    help="fold the sidecar into the input file and exit (makes no API calls)")
    args = ap.parse_args(argv)

    def log(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    if args.merge:
        return merge(args.input, args.sidecar, log=log)

    want_q = {s.strip() for s in args.questions.split(",") if s.strip()}
    want_s = {s.strip() for s in args.seats.split(",") if s.strip()}
    unknown = (want_q - {q for q, _ in QUESTIONS}) | (want_s - {s.label for s in PANEL})
    if unknown:
        log(f"unknown --questions/--seats name(s): {sorted(unknown)}")
        return 2
    questions = [q for q in QUESTIONS if not want_q or q[0] in want_q]
    seats = [s for s in PANEL if not want_s or s.label in want_s]
    missing = [p for _, p in questions if not p.is_file()]
    if missing:
        for p in missing:
            log(f"judge prompt not found: {p}")
        return 2

    rows = load_rows(args.input)
    if args.limit:
        rows = rows[: args.limit]
    skip = set() if (args.overwrite or args.dry_run) else done_keys(args.sidecar)
    if skip:
        log(f"resuming: {len(skip)} (prompt, question, seat) already judged in {args.sidecar}")

    # Every prompt is resolved and hash-checked BEFORE any call is made: a bad step file should
    # cost nothing, not surface 900 calls into a paid run.
    tasks: List[_Task] = []
    for row in rows:
        system, user, sha = rendered_pair(row, root=args.root)
        for qname, _ in questions:
            for seat in seats:
                if (row.get("run_dir"), row.get("step"), qname, seat.label) in skip:
                    continue
                tasks.append(_Task(row=row, question=qname, seat=seat,
                                   system=system, user=user, sha=sha))
    log(f"{len(rows)} prompts x {len(questions)} questions x {len(seats)} seats -> "
        f"{len(tasks)} calls")
    if not tasks:
        log("nothing to do")
        return 0

    bodies = {qname: load_prompt_template(path) for qname, path in questions}

    if args.dry_run:
        for task in tasks:
            print(f"\n{'=' * 78}\n# question={task.question} seat={task.seat.label} "
                  f"model={task.seat.provider}:{task.seat.model} pin={task.seat.pin} "
                  f"run={task.row.get('run_dir')} step={task.row.get('step')}\n{'=' * 78}")
            print(fill(bodies[task.question], task.system, task.user, VIEW))
        log(f"\ndry run: {len(tasks)} prompts built, no API calls made")
        return 0

    panels = {
        s.label: _Panel(
            seat=s,
            caller=make_judge_caller(
                provider=s.provider, model=s.model, max_tokens=MAX_TOKENS,
                temperature=s.temperature, reasoning_effort=s.reasoning_effort,
                provider_routing=s.pin,
            ),
            sem=threading.Semaphore(s.max_workers),
        )
        for s in seats
    }

    writer = _Writer(args.sidecar)
    n_ok = n_fail = 0
    try:
        # The pool is sized for all seats at once; each seat's own semaphore is what keeps it
        # inside its rate limit, so a slow Azure seat cannot starve the OpenRouter ones.
        with ThreadPoolExecutor(max_workers=sum(s.max_workers for s in seats)) as pool:
            futures = {
                pool.submit(run_task, panels[t.seat.label], bodies[t.question], t): t
                for t in tasks
            }
            for i, fut in enumerate(as_completed(futures), 1):
                task = futures[fut]
                tag = (f"{task.question}/{task.seat.label} "
                       f"{str(task.row.get('run_dir','')).split('/')[-1]} step {task.row.get('step')}")
                try:
                    row = fut.result()
                except Exception as exc:     # a call that died outside _call_json's own retries
                    row = _verdict_row(task, {"_parse_error": f"{type(exc).__name__}: {exc}",
                                              "_meta": {}})
                writer.write(row)
                if row["ok"]:
                    n_ok += 1
                else:
                    n_fail += 1
                    log(f"  [{i}/{len(tasks)}] FAIL {tag}: {row.get('parse_error')}")
                if i % 20 == 0 or i == len(tasks):
                    log(f"  [{i}/{len(tasks)}] ok={n_ok} fail={n_fail}")
    finally:
        writer.close()

    log(f"wrote {n_ok + n_fail} verdicts to {args.sidecar} (ok={n_ok}, fail={n_fail})")
    for label, p in panels.items():
        snap = getattr(p.caller, "snapshot", lambda: {})()
        log(f"  {label}: {snap.get('calls')} calls, {snap.get('total_tokens')} tokens, "
            f"${snap.get('cost_usd', 0):.4f}")
    if n_fail:
        log("rerun the same command to retry only the failures")
    log(f"then: python -m experiments.social_jira4.meta_judge_sweep --merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
