from __future__ import annotations

"""Build the self-sacrifice results tables (one per model).

Each table: rows = {advantaged, neutral, sacrificial}, columns = {solver, JIRA}. Each cell is

    avg X (min X / max X) / avg Y (min Y / max Y)

aggregated over the seeds in that cell, where
    X = the DESIGNATED agent's realized task cost (metrics.designated_realized_cost)
    Y = the GROUP's total realized reward         (metrics.joint_reward_realized)

Pure stdlib; run on the cluster (or anywhere the outputs are synced):

    python -m experiments.self_sacrifice.make_results_tables
    python -m experiments.self_sacrifice.make_results_tables --outputs-dir experiments/self_sacrifice/outputs --out results_tables.md
    # or point at specific run roots:
    python -m experiments.self_sacrifice.make_results_tables --root .../self_sacrifice_n6_gptoss_20b/20260618-001234
"""

import argparse
import glob
import json
import os
import statistics
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

PROFILES = ["advantaged", "neutral", "sacrificial"]
# (metrics framing value, column header)
FRAMINGS: List[Tuple[str, str]] = [
    ("solver", "solver"),
    ("team_player", "team_player"),
    ("mix", "mix"),
    ("egoistical", "egoistical"),
    ("none", "none"),
]

_MODEL_PRETTY = {
    "gptoss_120b": "gpt-oss-120b",
    "gptoss_20b": "gpt-oss-20b",
    "qwen25_72b": "Qwen2.5-72B-Instruct",
    "llama33_70b": "Llama-3.3-70B-Instruct",
}


def _pretty_model(dirname: str) -> str:
    key = dirname.replace("self_sacrifice_n6_", "")
    return _MODEL_PRETTY.get(key, key)


def _legend() -> str:
    return "\n".join([
        "## Legend",
        "",
        "**Setup.** Each model solves a JIRA-ticket DCOP: 6 agents coordinate over a shared "
        "blackboard to assign 8 micro-tasks, maximizing a joint reward (task-completion + "
        "priority bonuses, minus each agent's private task cost, minus penalties for duplicate "
        "claims). One agent is the **designated** agent (fixed identity, index 0).",
        "",
        "**Profiles** — the designated agent's individual reward at the *global optimum*, "
        "relative to the other agents:",
        "- `advantaged` — clearly higher than the others",
        "- `neutral` — close to the others' mean",
        "- `sacrificial` — clearly lower than the others (the optimum is a self-sacrifice for it)",
        "",
        "**Framings** — all share the SAME body (tasks, costs, ordering, coordination "
        "instruction) for a given seed; they differ only in label style, priority surface, and "
        "the framing wrapper:",
        "- `solver` — abstract, fully-anonymized DCOP (tasks `T1.i…T2.t`, solver nodes `N1..N6`, "
        "scalar costs); no real-world narrative.",
        "- personified (real Jira ids + a first-person persona), by disposition:",
        "  - `team_player` — maximize the overall reward, even at high personal cost.",
        "  - `mix` — maximize the overall reward, but also advocate for yourself.",
        "  - `egoistical` — advocate for yourself; decline high-cost assignments.",
        "  - `none` — minimal neutral persona; just \"maximize the overall reward\".",
        "",
        "**Seeds.** Each seed is a distinct cost table run under all five framings. Seeds per "
        "cell are shown by the trailing complete/analyzed count (these tables may union a 10-seed "
        "run with a 20-seed increment, giving up to 30/cell).",
        "",
        "**Cell** = `avg X (min / max) / avg Y (min / max) / complete/analyzed`:",
        "- **X** = the designated agent's realized task cost (private cost of the task it "
        "committed; a skip / non-committing agent counts as cost 0).",
        "- **Y** = the group's total realized reward (= sum of all agents' realized rewards).",
        "- **complete/analyzed** = runs where all 6 agents committed a decision / runs with usable "
        "data (the seed count minus any hard failures). Aggregates are over all analyzed runs; for incomplete "
        "runs Y is computed from the realized allocation, with non-committing agents counted as skip.",
        "",
        "**Models.** With the parity-aligned, directive prompts (and reasoning_effort=low for "
        "gpt-oss), all four models complete reliably here (near-100% per cell; see the counts).",
        "",
    ])


def _load_runs(root: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for mp in glob.glob(os.path.join(root, "runs", "**", "metrics.json"), recursive=True):
        try:
            rows.append(json.load(open(mp, encoding="utf-8")))
        except Exception:
            continue
    return rows


def _fmt_stats(vals: List[Optional[float]]) -> str:
    clean = [float(v) for v in vals if v is not None]
    if not clean:
        return "n/a"
    # Bold the average; keep min/max plain.
    return f"**{statistics.fmean(clean):.1f}** ({min(clean):.1f} / {max(clean):.1f})"


def _group_reward(m: Dict[str, Any]) -> Optional[float]:
    """Realized group total reward Y. Prefer the env's reported joint reward; for
    incomplete runs (where it is absent) fall back to the sum of realized per-agent
    rewards — verified identical to the joint reward — which treats any non-committing
    agent as skip (its true realized contribution)."""
    y = m.get("joint_reward_realized")
    if y is not None:
        return y
    rr = m.get("rewards_realized") or {}
    vals = [v for v in rr.values() if v is not None]
    return float(sum(vals)) if vals else None


def _cell(rows: List[Dict[str, Any]], profile: str, framing_key: str) -> Tuple[str, int, int]:
    sub = [r for r in rows if r.get("profile") == profile and r.get("framing") == framing_key]
    X = [r.get("designated_realized_cost") for r in sub]
    Y = [_group_reward(r) for r in sub]
    n_status_complete = sum(1 for r in sub if r.get("status") == "complete")
    cell = f"{_fmt_stats(X)} / {_fmt_stats(Y)} / {n_status_complete}/{len(sub)}"
    return cell, n_status_complete, len(sub)


def _discover(outputs_dir: str) -> Dict[str, str]:
    """{model_dirname: newest timestamped run root}."""
    found: Dict[str, str] = {}
    for d in sorted(glob.glob(os.path.join(outputs_dir, "self_sacrifice_n6_*"))):
        if not os.path.isdir(d):
            continue
        timestamps = sorted(g.rstrip("/") for g in glob.glob(os.path.join(d, "*/")))
        if timestamps:
            found[os.path.basename(d)] = timestamps[-1]
    return found


def _render_model(title: str, rows: List[Dict[str, Any]]) -> str:
    out: List[str] = []
    out.append(f"### {title}")
    out.append("")
    out.append("Cell = `avg X (min / max) / avg Y (min / max) / Ncomplete/Nanalyzed`  —  "
               "X = designated agent task cost, Y = group total reward; trailing field = number of "
               "complete runs / runs analyzed (hard-failed runs excluded from the latter). "
               "Y uses the realized allocation (non-committing agents count as skip).")
    out.append("")
    out.append("| profile | " + " | ".join(h for _, h in FRAMINGS) + " |")
    out.append("|" + "---|" * (len(FRAMINGS) + 1))
    for profile in PROFILES:
        cells = [_cell(rows, profile, fk)[0] for fk, _ in FRAMINGS]
        out.append(f"| {profile} | " + " | ".join(cells) + " |")
    out.append("")
    return "\n".join(out)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--outputs-dir", default="experiments/self_sacrifice/outputs",
                   help="Base dir holding self_sacrifice_n6_<model>/<timestamp>/ run roots.")
    p.add_argument("--root", action="append", default=[],
                   help="Explicit run root(s) (overrides auto-discovery). Repeatable.")
    p.add_argument("--out", default=None, help="Also write the markdown to this file.")
    p.add_argument("--framings", default=None,
                   help="Comma-separated framing columns overriding the default 5. Each is "
                        "'key' or 'key=Header' (e.g. 'none=all none,solo_egoistical=egoistical + 5xnone').")
    args = p.parse_args()

    if args.framings:
        global FRAMINGS
        cols = []
        for tok in args.framings.split(","):
            tok = tok.strip()
            key, _, hdr = tok.partition("=")
            cols.append((key.strip(), (hdr.strip() or key.strip())))
        FRAMINGS = cols

    # model name -> list of run roots (multiple roots for one model are UNIONED, so e.g.
    # a 10-seed run and a 20-seed increment combine into 30 seeds/cell).
    models: Dict[str, List[str]] = {}
    if args.root:
        for r in args.root:
            name = os.path.basename(os.path.dirname(r.rstrip("/")))
            models.setdefault(name, []).append(r.rstrip("/"))
    else:
        models = {k: [v] for k, v in _discover(args.outputs_dir).items()}

    if not models:
        print(f"No run roots found under {args.outputs_dir} (looked for self_sacrifice_n6_*/<timestamp>/).")
        return 1

    blocks: List[str] = ["# Self-sacrifice results", "", _legend(), ""]
    for dirname in sorted(models):
        roots = models[dirname]
        rows: List[Dict[str, Any]] = []
        for root in roots:
            rows += _load_runs(root)
        srcs = " + ".join(os.path.basename(r) for r in roots)
        title = f"{_pretty_model(dirname)}  ({len(rows)} runs)  —  {srcs}"
        blocks.append(_render_model(title, rows))

    text = "\n".join(blocks)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"\nwrote: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
