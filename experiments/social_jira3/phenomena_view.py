#!/usr/bin/env python3
"""Interactive HTML phenomena view for social_jira3 judge results.

Thin adapter over ``experiments.social_common.phenomena_view``: defines this experiment's
7 sweep axes, its taxonomy (parsed from JUDGE_TURN_PROMPT.md), the level-2 phenomena to fold
in, and the default multi-dir scan; the shared core does the aggregation and renders the
(identical-across-experiments) HTML shell.

This is the interactive HTML counterpart to ``phenomena_hist.py`` (which prints text tables):
the metric toggle expresses ``--rollout`` (per-turn rate vs per-run "ever"), the split/filter
sidebar expresses the ``--model``/``--conf``/... axis flags, and an "include incomplete" toggle
expresses ``--include-incomplete``. Clicking a cell drills into the flagged turns behind it.

Usage:
    python -m experiments.social_jira3.phenomena_view [<root> ...] [--out FILE] [--open]

With no root, scans every outputs/social_jira3_c2p2_*_conflict_quit23_v5_confsweep* dir
(all models, incl. the *_nodecoy variants) — the same default set as phenomena_hist.py.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict

from experiments.social_common.phenomena_view import Adapter, run_cli

HERE = Path(__file__).resolve().parent
JUDGE_PROMPT_PATH = HERE / "JUDGE_TURN_PROMPT.md"
DEFAULT_GLOB = "social_jira3_c2p2_*_conflict_quit23_v5_confsweep*"

# Level-2 confirmation files (judge.py --level2), folded in as their own phenomena rows: each
# confirmed turn (present==True) counts once. Maps the per-run L2 filename -> display name.
L2_PHENOMENA = {
    "judge_l2_fabrication_executed.json": "L2 Fabrication (executed)",
    "judge_l2_hallucination.json": "L2 Hallucination",
}

_SAMPLE_RE = re.compile(r"__s(\d+)(?:$|/)")

# Sweep axes that fully identify a rollout (model/decoy/hint/seed/sample/conf/quit), mapped to
# the judge_results.json body field that carries each. `sample` isn't in the body — it's derived
# from the run-dir name below.
DIMENSIONS = [
    ("model_label", "model"),
    ("decoys", "decoy"),
    ("hint", "hint"),
    ("seed", "seed"),
    ("sample", "sample"),
    ("confidentiality", "conf"),
    ("dislike_strength", "quit"),
]


def _model_alias(summary: Dict[str, Any], _path: Path) -> str:
    """Collapse the verbose model_label to the short axis value qwen|gpt-oss|glm."""
    s = str(summary.get("model_label") or summary.get("model") or "").strip().lower()
    if "qwen" in s:
        return "qwen"
    if "gpt" in s and "oss" in s:
        return "gpt-oss"
    if "glm" in s:
        return "glm"
    return s or "?"


def _sample(summary: Dict[str, Any], _path: Path) -> str:
    """Recover the sample index from the run_dir name (``…__s2``); the body has no sample field."""
    m = _SAMPLE_RE.search(str(summary.get("run_dir") or ""))
    return m.group(1) if m else "?"


def taxonomy() -> list[str]:
    """Phenomenon names in display order, parsed from the judge prompt's ``## Phenomenon
    Taxonomy`` section (bold ``**Name**`` headers on their own line)."""
    try:
        names, in_section = [], False
        for ln in JUDGE_PROMPT_PATH.read_text(encoding="utf-8").splitlines():
            s = ln.strip()
            if s.startswith("## "):
                in_section = s == "## Phenomenon Taxonomy"
                continue
            if in_section:
                m = re.match(r"^\*\*([^*{}]+)\*\*$", s)  # skip template headers like **{agent}...**
                if m:
                    names.append(m.group(1).strip())
        return names
    except Exception:
        return []


def default_roots() -> list[Path]:
    return sorted((HERE / "outputs").glob(DEFAULT_GLOB))


# Optional extra dir foldable in via the top-right switch (default off): v6 qwen seeds 3-6, which
# is off-glob (v6, not v5) so it is never in the default scan unless the switch is flipped.
EXTRA_ROOTS = [HERE / "outputs" / "social_jira3_c2p2_qwen3_6_35b_a3b_conflict_quit23_v6_confsweep_seeds3456"]

ADAPTER = Adapter(
    key="social_jira3",
    title="social_jira3 — judge phenomena",
    here=HERE,
    dimensions=DIMENSIONS,
    taxonomy=taxonomy,
    default_roots=default_roots,
    l2_map=L2_PHENOMENA,
    derive={"model_label": _model_alias, "sample": _sample},
    extra_roots=EXTRA_ROOTS,
    extra_label="include v6 qwen seeds 3–6",
)


if __name__ == "__main__":
    raise SystemExit(run_cli(ADAPTER, sys.argv[1:]))
