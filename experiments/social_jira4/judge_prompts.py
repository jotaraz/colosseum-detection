from __future__ import annotations

"""Offline judging of the v3 rendered prompts (``reports/v3_prompts_rendered.jsonl``).

Every row of that file is one optimizer step, carrying the prompt the prompter produced:
``rendered_system`` (the block-assembled system prompt — the thing being optimized) and
``rendered_user`` (the world state it was rendered against: roster, tasks, private info, decoys).

This script re-reads those prompts and has N **independent judges** score them. A judge is nothing
but a prompt file plus a model — see ``Judge`` — so adding one is a registry line, not code.

Two **views** of every prompt, judged separately so the pair is a controlled comparison:

  * ``system``      — the system prompt alone.
  * ``system_user`` — system + the user prompt it was rendered against.

Judges are **blind**: they see only the prompt text. None of the row's own metadata (``score_mean``,
``gate``, ``cb_reason``, ``best_lie``, the prompter's rationale) is ever placed in a judge prompt, so
a verdict is an independent read of the prompt rather than a review of the pipeline's own verdict.

Judge prompt files follow the convention of the other prompts in this directory: prose header, a
``---`` separator, then the fillable body (``load_prompt_template`` returns everything after it).
Placeholders available in the body:

    {system_prompt}   the rendered system prompt
    {user_prompt}     the rendered user prompt — in the ``system`` view this is replaced by
                      ``_USER_WITHHELD``, so write the body to tolerate its absence
    {judged_prompt}   both of the above under ``=== SYSTEM ===`` / ``=== USER ===`` headers,
                      already collapsed for the view — the simplest thing to interpolate

The judge is asked for ONE JSON object (``checks_balances._call_json`` handles fencing, chatty
replies, empty completions and retries). The envelope is not fixed here: whatever the judge returns
is stored whole under ``verdict``, with ``score`` / ``label`` / ``rationale`` lifted to the top level
when present so the common case joins without digging.

Output is append-only JSONL, one row per (prompt, judge, view), keyed for a join back onto the input
on ``(run_dir, step)``. Reruns are cheap: rows that already parsed are skipped, so a second pass only
patches the calls that failed. Identical prompt text is called once and the verdict fanned out
(58 of the 68 v3 rows are distinct), and rows whose candidate never rendered (3 gate-rejected steps
have ``rendered_system: null``) are skipped with a note.

Usage (from the repo root):
    python -m experiments.social_jira4.judge_prompts \
        --judge coercion=JUDGE_COERCION.md \
        --judge realism=JUDGE_REALISM.md@openai/gpt-5.4
    python -m experiments.social_jira4.judge_prompts --judge x=X.md --dry-run --limit 2
    python -m experiments.social_jira4.judge_prompts --judge x=X.md --views system
"""

import argparse
import hashlib
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from experiments.social_jira3.judge import load_prompt_template
from experiments.social_jira4.checks_balances import _call_json
from experiments.social_jira4.llm import (
    DEFAULT_JUDGE_MODEL,
    DEFAULT_JUDGE_PROVIDER,
    DEFAULT_JUDGE_REASONING_EFFORT,
    Caller,
    make_judge_caller,
)

HERE = Path(__file__).resolve().parent
DEFAULT_INPUT = HERE / "reports" / "v3_prompts_rendered.jsonl"
DEFAULT_OUTPUT = HERE / "reports" / "v3_judge_verdicts.jsonl"

VIEWS = ("system", "system_user")
# What ``{user_prompt}`` becomes in the system-only view. Named rather than blank so a judge that
# interpolates it cannot mistake the omission for an empty user prompt.
_USER_WITHHELD = "(not provided — judge the system prompt on its own)"
_PLACEHOLDERS = ("system_prompt", "user_prompt", "judged_prompt")


# --------------------------------------------------------------------------------- judges
@dataclass(frozen=True)
class Judge:
    """One independent judge: a prompt file, a provider and a model. The rest has a default."""

    name: str
    prompt_file: Path
    provider: str = DEFAULT_JUDGE_PROVIDER
    model: str = ""                 # "" -> llm.DEFAULT_JUDGE_MODEL
    temperature: float = 0.0        # judges are deterministic
    max_tokens: int = 4096
    reasoning_effort: str = DEFAULT_JUDGE_REASONING_EFFORT
    retries: int = 2

    @property
    def resolved_model(self) -> str:
        return self.model or DEFAULT_JUDGE_MODEL


@dataclass(frozen=True)
class JudgeModel:
    """One model the whole panel of prompts is run through. ``label`` is what lands in the output."""

    label: str
    provider: str
    model: str


# ---- the meta-judge panel (meta-judges-feeling.md) -----------------------------------------
# Six questions x every model below. Each question is asked in the same voice; what varies is how
# much the judge is told about what we are looking for. #6 is deliberately open-ended and never
# names deception — its signal is whether fabrication comes up unprompted, so do not "improve" it
# by making it concrete.
META_JUDGE_PROMPTS: List[Tuple[str, Path]] = [
    ("realism", HERE / "META_JUDGE_1_REALISM.md"),
    ("fabrication", HERE / "META_JUDGE_2_FABRICATION.md"),
    ("admissibility", HERE / "META_JUDGE_3_ADMISSIBILITY.md"),
    ("forecast", HERE / "META_JUDGE_4_FORECAST.md"),
    ("intent", HERE / "META_JUDGE_5_INTENT.md"),
    ("coldread", HERE / "META_JUDGE_6_COLDREAD.md"),
]

# Two providers on purpose: DeepSeek-V4-Pro also wrote these prompts and ran the CB gate that
# passed them, so on its own it would be grading its own homework. The Azure deployment name is
# read from the env the cluster job sources (``AZURE_JUDGE_DEPLOYMENT``), defaulting to gpt-5.4.
META_JUDGE_MODELS: List[JudgeModel] = [
    JudgeModel("dspro", "openrouter", "deepseek/deepseek-v4-pro"),
    JudgeModel("gpt54", "azure", os.getenv("AZURE_JUDGE_DEPLOYMENT", "gpt-5.4")),
]


def build_panel(
    prompts: Sequence[Tuple[str, Path]] = tuple(META_JUDGE_PROMPTS),
    models: Sequence[JudgeModel] = tuple(META_JUDGE_MODELS),
) -> List[Judge]:
    """The cross product — every question asked by every model. Names are ``question@model``."""
    return [
        Judge(name=f"{qname}@{m.label}", prompt_file=path, provider=m.provider, model=m.model)
        for qname, path in prompts
        for m in models
    ]


JUDGES: List[Judge] = build_panel()


def parse_judge_spec(spec: str) -> Judge:
    """``name=FILE.md``, ``name=FILE.md@model`` or ``name=FILE.md@provider:model`` -> ``Judge``.

    Relative prompt files resolve against this directory. Without a provider the default
    (OpenRouter) is used, so ``x=X.md@deepseek/deepseek-v4-pro`` keeps working."""
    if "=" not in spec:
        raise argparse.ArgumentTypeError(
            f"--judge expects name=FILE.md[@[provider:]model], got {spec!r}")
    name, rhs = spec.split("=", 1)
    file_part, _, model_part = rhs.partition("@")
    provider, sep, model = model_part.partition(":")
    if not sep:  # no "provider:" prefix — the whole thing is the model
        provider, model = DEFAULT_JUDGE_PROVIDER, model_part
    path = Path(file_part).expanduser()
    if not path.is_absolute():
        path = HERE / path
    name = name.strip()
    if not name:
        raise argparse.ArgumentTypeError(f"--judge has an empty name: {spec!r}")
    return Judge(name=name, prompt_file=path, provider=provider.strip(), model=model.strip())


# --------------------------------------------------------------------------------- prompts
def judged_text(system: str, user: str, view: str) -> str:
    """The prompt text a judge reads for ``view`` — also what ``prompt_sha`` is taken over."""
    if view == "system":
        return f"=== SYSTEM ===\n{system}"
    if view == "system_user":
        return f"=== SYSTEM ===\n{system}\n\n=== USER ===\n{user}"
    raise ValueError(f"unknown view {view!r} (expected one of {VIEWS})")


def fill(body: str, system: str, user: str, view: str) -> str:
    """Substitute only the named placeholders — JSON braces in the template must survive."""
    values = {
        "system_prompt": system,
        "user_prompt": user if view == "system_user" else _USER_WITHHELD,
        "judged_prompt": judged_text(system, user, view),
    }
    out = body
    for key in _PLACEHOLDERS:
        out = out.replace("{" + key + "}", values[key])
    return out


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------------- io
def load_rows(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


Key = Tuple[str, Any, str, str]  # (run_dir, step, judge, view)


def done_keys(path: Path) -> set:
    """Keys already judged **successfully**. Failed rows are left out so a rerun retries them.

    The file is append-only and last-wins on read, so a retry's row simply supersedes the failure.
    """
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
                continue  # a truncated tail from an interrupted run — it will just be redone
            key = (r.get("run_dir"), r.get("step"), r.get("judge"), r.get("view"))
            if r.get("ok"):
                done.add(key)
            else:
                done.discard(key)
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


# --------------------------------------------------------------------------------- judging
@dataclass
class _Task:
    row: Dict[str, Any]
    judge: Judge
    view: str
    system: str
    user: str
    sha: str


@dataclass
class _Panel:
    """A judge's loaded body + its caller + the per-run verdict cache for identical prompts."""

    judge: Judge
    body: str
    caller: Caller
    cache: Dict[Tuple[str, str], Dict[str, Any]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


def _verdict_row(task: _Task, obj: Dict[str, Any]) -> Dict[str, Any]:
    """One output row: join keys, what was judged, and the verdict with its trail."""
    obj = dict(obj)
    meta = obj.pop("_meta", {})
    parse_error = obj.pop("_parse_error", None)
    row = task.row
    out: Dict[str, Any] = {
        # join keys back onto the input
        "run_dir": row.get("run_dir"),
        "step": row.get("step"),
        "variant": row.get("variant"),
        "target_model": row.get("target_model"),
        "warm": row.get("warm"),
        # who judged what
        "judge": task.judge.name,
        "judge_provider": task.judge.provider,
        "judge_model": task.judge.resolved_model,
        "view": task.view,
        "prompt_sha": task.sha,
        "judged_chars": len(judged_text(task.system, task.user, task.view)),
        "ok": parse_error is None,
    }
    if parse_error is not None:
        out["parse_error"] = parse_error
    # Lift the common fields when the judge happens to emit them; the whole object is kept anyway.
    for k in ("score", "label", "rationale"):
        if k in obj:
            out[k] = obj[k]
    out["verdict"] = obj
    out["reasoning"] = meta.get("reasoning", "")
    out["usage"] = meta.get("usage", {})
    out["attempts"] = meta.get("attempts")
    out["raw"] = meta.get("raw", "")
    return out


def run_task(panel: _Panel, task: _Task) -> Dict[str, Any]:
    """Judge one (prompt, judge, view), reusing a verdict already obtained for identical text."""
    cache_key = (task.sha, task.view)
    with panel.lock:
        hit = panel.cache.get(cache_key)
    if hit is not None:
        return _verdict_row(task, hit)
    filled = fill(panel.body, task.system, task.user, task.view)
    obj = _call_json(panel.caller, filled, retries=panel.judge.retries)
    if "_parse_error" not in obj:  # never cache a failure — a retry deserves a fresh call
        with panel.lock:
            panel.cache[cache_key] = obj
    return _verdict_row(task, obj)


def build_tasks(
    rows: Sequence[Dict[str, Any]],
    judges: Sequence[Judge],
    views: Sequence[str],
    skip: set,
    *,
    log: Callable[[str], None],
) -> List[_Task]:
    tasks: List[_Task] = []
    for row in rows:
        system, user = row.get("rendered_system"), row.get("rendered_user")
        if not system:
            # Gate-rejected steps never rendered a candidate; there is nothing to judge.
            log(f"  skip {row.get('run_dir')} step {row.get('step')}: no rendered_system")
            continue
        for view in views:
            if view == "system_user" and not user:
                log(f"  skip {row.get('run_dir')} step {row.get('step')} [{view}]: no rendered_user")
                continue
            sha = _sha(judged_text(system, user or "", view))
            for judge in judges:
                key = (row.get("run_dir"), row.get("step"), judge.name, view)
                if key in skip:
                    continue
                tasks.append(_Task(row=row, judge=judge, view=view, system=system,
                                   user=user or "", sha=sha))
    return tasks


# --------------------------------------------------------------------------------- cli
def main(argv: Optional[Sequence[str]] = None) -> int:
    # Not ``__doc__``: the module string follows ``from __future__``, so it is not a docstring.
    ap = argparse.ArgumentParser(
        description="Judge the v3 rendered prompts with a panel of independent LLM judges."
    )
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--judge", action="append", default=[],
                    metavar="NAME=FILE.md[@[provider:]model]",
                    help="ad-hoc judge; repeatable. Without any, the JUDGES registry is used.")
    ap.add_argument("--prompts", default="",
                    help="comma-separated subset of the meta-judge questions "
                         f"({','.join(n for n, _ in META_JUDGE_PROMPTS)})")
    ap.add_argument("--models", default="",
                    help="comma-separated subset of the judge models "
                         f"({','.join(m.label for m in META_JUDGE_MODELS)}) — e.g. rerun just the "
                         "Azure half after a rate-limit run")
    ap.add_argument("--views", default=",".join(VIEWS),
                    help=f"comma-separated subset of {VIEWS}")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0, help="only the first N input rows (smoke test)")
    ap.add_argument("--overwrite", action="store_true",
                    help="re-judge everything instead of resuming past the rows already done")
    ap.add_argument("--dry-run", action="store_true",
                    help="build and print the filled judge prompts; make no API calls")
    args = ap.parse_args(argv)

    def log(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    if args.judge:
        judges = [parse_judge_spec(s) for s in args.judge]
    else:
        want_q = {s.strip() for s in args.prompts.split(",") if s.strip()}
        want_m = {s.strip() for s in args.models.split(",") if s.strip()}
        prompts = [p for p in META_JUDGE_PROMPTS if not want_q or p[0] in want_q]
        models = [m for m in META_JUDGE_MODELS if not want_m or m.label in want_m]
        unknown = (want_q - {n for n, _ in META_JUDGE_PROMPTS}) | (
            want_m - {m.label for m in META_JUDGE_MODELS})
        if unknown:
            log(f"unknown --prompts/--models name(s): {sorted(unknown)}")
            return 2
        judges = build_panel(prompts, models)
    if not judges:
        log("no judges: pass --judge NAME=FILE.md, or fill in the JUDGES registry")
        return 2
    missing = [j for j in judges if not j.prompt_file.is_file()]
    if missing:
        for j in missing:
            log(f"judge {j.name!r}: prompt file not found: {j.prompt_file}")
        return 2

    views = [v.strip() for v in args.views.split(",") if v.strip()]
    bad = [v for v in views if v not in VIEWS]
    if bad:
        log(f"unknown view(s) {bad} — expected a subset of {list(VIEWS)}")
        return 2

    rows = load_rows(args.input)
    if args.limit:
        rows = rows[: args.limit]
    skip = set() if (args.overwrite or args.dry_run) else done_keys(args.out)
    if skip:
        log(f"resuming: {len(skip)} (row, judge, view) already judged in {args.out}")

    tasks = build_tasks(rows, judges, views, skip, log=log)
    # "tasks", not "calls": identical prompt text is called once per judge and the verdict reused.
    n_distinct = len({(t.judge.name, t.sha) for t in tasks})
    log(f"{len(rows)} rows x {len(judges)} judges x {len(views)} views -> {len(tasks)} tasks "
        f"({n_distinct} distinct prompts to call)")
    if not tasks:
        log("nothing to do")
        return 0

    panels = {
        j.name: _Panel(
            judge=j,
            body=load_prompt_template(j.prompt_file),
            caller=(None if args.dry_run else make_judge_caller(
                provider=j.provider, model=j.resolved_model, max_tokens=j.max_tokens,
                temperature=j.temperature, reasoning_effort=j.reasoning_effort,
            )),
        )
        for j in judges
    }

    if args.dry_run:
        for task in tasks:
            panel = panels[task.judge.name]
            print(f"\n{'=' * 78}\n# judge={task.judge.name} "
                  f"model={task.judge.provider}:{task.judge.resolved_model} "
                  f"view={task.view} run={task.row.get('run_dir')} step={task.row.get('step')}\n"
                  f"{'=' * 78}")
            print(fill(panel.body, task.system, task.user, task.view))
        log(f"\ndry run: {len(tasks)} prompts built, no API calls made")
        return 0

    writer = _Writer(args.out)
    n_ok = n_fail = 0
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(run_task, panels[t.judge.name], t): t for t in tasks}
            for i, fut in enumerate(as_completed(futures), 1):
                task = futures[fut]
                tag = f"{task.judge.name}/{task.view} {task.row.get('run_dir','').split('/')[-1]}" \
                      f" step {task.row.get('step')}"
                try:
                    row = fut.result()
                except Exception as exc:  # a call that died outside _call_json's own retries
                    row = _verdict_row(task, {"_parse_error": f"{type(exc).__name__}: {exc}",
                                              "_meta": {}})
                writer.write(row)
                if row["ok"]:
                    n_ok += 1
                else:
                    n_fail += 1
                    log(f"  [{i}/{len(tasks)}] FAIL {tag}: {row.get('parse_error')}")
                if i % 10 == 0 or i == len(tasks):
                    log(f"  [{i}/{len(tasks)}] ok={n_ok} fail={n_fail}")
    finally:
        writer.close()

    totals = {n: p.caller.snapshot() for n, p in panels.items() if p.caller is not None}
    log(f"wrote {n_ok + n_fail} verdicts to {args.out} (ok={n_ok}, fail={n_fail})")
    for name, t in totals.items():
        log(f"  {name}: {t['calls']} calls, {t['total_tokens']} tokens")
    if n_fail:
        log("rerun the same command to retry only the failures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
