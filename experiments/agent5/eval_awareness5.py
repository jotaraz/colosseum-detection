"""Eval-awareness judge over agent5 rollouts — agent1's ev1 judge on agent5 records.

    .venv/bin/python -m experiments.agent5.eval_awareness5 \
        --runs 'experiments/agent5/runs/agent5_v16*_askReasonable_conc_*_2026*/run.json' \
        --workers 6

Reuses ``experiments.agent1.eval_awareness_judge`` wholesale (same CRITIC_EVALAWARE.md
prompt — the world is the same tanager sprint morning — same chunking, parsing, span
verification and collapse). What is adapted is only the record shape:

- CoT rows come from each turn's ``reasoning_full`` (per-step reasoning as merged from
  the proxy dump), labeled ``<turn>.<step>`` so spans trace back to the numbered html.
- Judged text is the chain-of-thought ONLY — the ``text_to_principal`` private log is
  not judged, matching agent1's cot_evalaware scope.
- Identity comes from the run-dir name (``agent5_<fixture>_<arm>_conc_<model>_s<seed>``)
  plus the config; ``fixture_tag`` (v16c/v16z) is recorded since every Slack fixture
  reports version "slack_v1".

Sidecar: ``<run_dir>/run.cot_evalaware_<jv>_<model>[_rN].json`` (same resume semantics).
"""

from __future__ import annotations

import argparse
import glob as globmod
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from concurrent.futures import ThreadPoolExecutor

from experiments.agent1 import eval_awareness_judge as ej
from experiments.social_jira3.judge import load_prompt_template
from experiments.social_jira4.llm import make_judge_caller

logger = logging.getLogger("experiments.agent5.eval_awareness5")

_DIR_RE = re.compile(
    r"^agent5_(?P<fixture>v\d+[a-z]?)_(?P<arm>[A-Za-z]+)_conc_(?P<model>[a-z0-9]+?)"
    r"(?:_s(?P<seed>\d+))?_\d{8}-\d{6}$")


def reasoning_rows(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """agent1-shaped {agent, step, reasoning} rows from an agent5 record."""
    rows: List[Dict[str, Any]] = []
    for i, t in enumerate(run.get("turns") or []):
        pieces = t.get("reasoning_full") or t.get("reasoning") or []
        if isinstance(pieces, str):
            pieces = [pieces]
        for j, txt in enumerate(pieces):
            if str(txt or "").strip():
                rows.append({"agent": str(t.get("agent") or ""),
                             "step": f"{i}.{j}", "reasoning": str(txt)})
    return rows


def identity(path: Path, run: Dict[str, Any]) -> Dict[str, Any]:
    cfg = run.get("config") or {}
    m = _DIR_RE.match(path.parent.name)
    return {
        "run": path.parent.name,
        "path": str(path),
        "fixture_tag": (m.group("fixture") if m else ""),
        "arm": (m.group("arm") if m else ""),
        "model_short": (m.group("model") if m else ""),
        "model": str(cfg.get("model") or ""),
        "seed": cfg.get("seed"),
        "outcome": str(run.get("outcome") or ""),
    }


def judge_run(run_path: Path, *, caller, template, model, replicate, workers,
              force, chunk_chars, fallback=None) -> Dict[str, Any]:
    run = json.loads(run_path.read_text(encoding="utf-8"))
    units = ej.chunks({"reasoning": reasoning_rows(run)}, chunk_chars=chunk_chars)
    out_path = ej.sidecar_path(run_path, model, replicate)
    existing: Dict[Any, Dict[str, Any]] = {}
    if out_path.exists() and not force:
        try:
            prev = json.loads(out_path.read_text(encoding="utf-8"))
            for row in prev.get("chunks") or []:
                if row.get("verdict") is not None:
                    existing[(row.get("agent"), row.get("chunk"))] = row
        except Exception:
            pass
    todo = [u for u in units if (u["agent"], u["chunk"]) not in existing]
    rows = list(existing.values())
    if todo:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for fut in [pool.submit(ej.judge_chunk, caller, template, u,
                                    fallback=fallback) for u in todo]:
                rows.append(fut.result())
    rows.sort(key=lambda r: (r.get("agent") or "", r.get("chunk") or 0))
    record = {
        **identity(run_path, run),
        "judge": "eval-awareness",
        "judge_version": ej.JUDGE_VERSION,
        "judge_model": model,
        "replicate": replicate,
        "n_chunks": len(units),
        "n_judged_now": len(todo),
        "n_errors": sum(1 for r in rows if r.get("error") or r.get("parse_error")),
        "n_retried": sum(1 for r in rows if (r.get("_meta") or {}).get("retried")),
        **ej._collapse(rows),
        "chunks": rows,
    }
    out_path.write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
    return record


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--judge-model", default="deepseek/deepseek-v4-flash-0731")
    ap.add_argument("--provider", default="openrouter")
    ap.add_argument("--pin-provider", default="")
    ap.add_argument("--reasoning-effort", default="medium")
    ap.add_argument("--max-tokens", type=int, default=6000)
    ap.add_argument("--chunk-chars", type=int, default=60000)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--replicate", type=int, default=1)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    paths = sorted({Path(p) for pat in args.runs for p in globmod.glob(pat)})
    paths = [p for p in paths if p.name == "run.json"]
    template = load_prompt_template(ej._PROMPT)

    if args.dry_run:
        total = 0
        for path in paths:
            us = ej.chunks({"reasoning": reasoning_rows(
                json.loads(path.read_text()))}, chunk_chars=args.chunk_chars)
            print(f"{path.parent.name}: {len(us)} chunks, "
                  f"{sum(len(u['trace']) for u in us)} chars")
            total += len(us)
        print(f"=== {len(paths)} runs, {total} chunks ===")
        return 0

    routing = ({"order": [args.pin_provider], "allow_fallbacks": False}
               if args.pin_provider else None)
    kw = dict(provider=args.provider, model=args.judge_model, temperature=0.0,
              reasoning_effort=args.reasoning_effort, provider_routing=routing)
    caller = make_judge_caller(max_tokens=args.max_tokens, **kw)
    fallback = make_judge_caller(max_tokens=args.max_tokens * 2, **kw)

    done = 0
    for path in paths:
        try:
            rec = judge_run(path, caller=caller, template=template,
                            model=args.judge_model, replicate=args.replicate,
                            workers=args.workers, force=args.force,
                            chunk_chars=args.chunk_chars, fallback=fallback)
        except Exception as exc:
            logger.error("FAILED %s: %s", path.parent.name, exc)
            continue
        done += 1
        logger.info("%s  chunks=%d new=%d errors=%d  run=%s aware=%d ambiguous=%d",
                    path.parent.name, rec["n_chunks"], rec["n_judged_now"],
                    rec["n_errors"], rec["run_verdict"], rec["n_agents_aware"],
                    rec["n_agents_ambiguous"])
    logger.info("%d/%d runs judged; usage=%s", done, len(paths),
                dict(getattr(caller, "totals", {}) or {}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
