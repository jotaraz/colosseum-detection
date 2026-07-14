#!/usr/bin/env python3
"""Interactive HTML phenomena view for social_jira2 judge results.

Thin adapter over ``experiments.social_common.phenomena_view``: defines this experiment's
axes, phenomenon taxonomy, and default scan dir; the shared core does the aggregation and
renders the (identical-across-experiments) HTML shell.

Usage:
    python -m experiments.social_jira2.phenomena_view [<root> ...] [--out FILE] [--open]

With no root, scans the standard merged conflict dir (gpt-oss only, so "split by model"
shows one column — pass other outputs/ dirs explicitly to compare models).
"""
from __future__ import annotations

import sys
from pathlib import Path

from experiments.social_common.phenomena_view import Adapter, run_cli

HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = HERE / "outputs" / "social_jira2_c2p2_gptoss_120b_medium_conflict_newgen_merged"

# An assistant type is (model, feelings_preset, scenario_type, setup, personality); seed/sample
# are summed over unless split. Field names are read straight from the judge_results.json body.
DIMENSIONS = [
    ("model_label", "model"),
    ("feelings_preset", "feelings"),
    ("scenario_type", "scenario"),
    ("setup", "setup"),
    ("personality", "personality"),
]

# Canonical phenomenon order (mirrors aggregate_judgements.py / JUDGE_TURN_PROMPT.md). Keeps a
# stable row order and surfaces phenomena that never fired.
PHENOMENA = [
    "Leakage",
    "Implied Leakage",
    "Strategic Omission",
    "Strategic Framing",
    "Cover Story Planning",
    "Observer Suspicion Modeling",
    "Strategic Attention Manipulation",
    "Fabrication (considered)",
    "Fabrication (executed)",
    "Hallucination",
    "Made Bad Deal",
    "Out-of-Scope Proposal",
    "Vague",
    "Ambiguous",
]

ADAPTER = Adapter(
    key="social_jira2",
    title="social_jira2 — judge phenomena",
    here=HERE,
    dimensions=DIMENSIONS,
    taxonomy=lambda: list(PHENOMENA),
    default_roots=lambda: [DEFAULT_ROOT],
)


if __name__ == "__main__":
    raise SystemExit(run_cli(ADAPTER, sys.argv[1:]))
