"""Build ``experiments/agent5/index.html``: a local browser over the experiment_plan.md cells.

    python experiments/agent5/index5.py            # writes index.html next to this file

Pick an experiment (1.a, 1.b, 6.b, ...) in the sidebar; the page shows its world's fixture
render (``fixtures/w1_html/<world>.html``) and a table of its rollouts (outcome, board
shape, pairs, turns), and opens any run's ``run.html`` or ``board.html`` in the frame.
Static file, relative links only, so it works from ``file://``. Run names that match no
plan item are grouped under their raw ``world / cell`` key.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"
OUT = HERE / "index.html"

#: (world, cell) -> plan label, from experiment_plan.md. ``cell`` includes arm suffixes.
PLAN = {
    ("w1PstrongNstrong_affBothNeutralPpl", "hzReasonableHelenaProbe"): "1.a  base cell",
    ("w1PstrongNstrong_affBothNeutralPpl", "hzReasonableHelenaProbeBlocks"): "1.b  agents identifiable (Blocks)",
    ("w1PstrongNstrong_confide1_affBothNeutralPpl", "hzReasonableHelenaProbe"): "1.c.i  confide 1",
    ("w1PstrongNstrong_confide2_affBothNeutralPpl", "hzReasonableHelenaProbe"): "1.c.ii  confide 2",
    ("w1PstrongNstrong_confide3_affBothNeutralPpl", "hzReasonableHelenaProbe"): "1.c.iii  confide 3",
    ("w1PstrongNstrong_confide3_affBothNeutralPpl", "hzReasonableHelenaProbeDmHint"): "1.c.iii-hint  confide 3 + DM-reading hint",
    ("w1PstrongNstrong_callumBereavement_affBothNeutralPpl", "hzReasonableHelenaProbe"): "1.d  Callum bereavement",
    ("w1PstrongNstrong_affBothNeutralPpl", "hzReasonableHelenaProbeCallumAsk"): "1.e  callumAsk",
    ("w1PstrongNstrong_affBothNeutralPpl_mBusy11", "hzReasonableHelenaProbeCallumAsk"): "1.e-busy  callumAsk, Matthieu busy 11–12",
    ("w1PstrongNstrong_affBothNeutralPpl_mBusy11", "hzReasonableHelenaProbeCallumAsk2"): "1.f  callumAsk2: Matthieu 09:31, others 09:35, busy",
    ("w1PsuperstrongNstrong_affBothNeutralPpl_mBusy11", "hzReasonableHelenaProbeCallumAsk"): "1.e-busy-super  callumAsk, Matthieu busy, Psuperstrong",
    ("w1PsuperstrongNstrong_affBothNeutralPpl", "hzReasonableHelenaProbe"): "1.a-super  Psuperstrong",
    ("w1PstrongNstrong", "hzReasonableHelenaProbe"): "2.a  no affBothNeutral",
    ("w1PstrongNstrong_affBothT1", "hzReasonableHelenaProbe"): "2.b  affBothT1",
    ("w1PdefNstrong_affBothNeutralPpl", "hzReasonableHelenaProbe"): "3.a  PdefNstrong",
    ("w1PsillyNstrong_affBothNeutralPpl", "hzReasonableHelenaProbe"): "3.b  PsillyNstrong",
    ("w1PbadNstrong_affBothNeutralPpl", "hzReasonableHelenaProbe"): "3.c  PbadNstrong",
    ("w1PNshared_affBothNeutralPpl", "hzReasonableHelenaProbe"): "3.d  PNshared",
    ("w1PstrongNstrong_affBothNeutralPpl", "hzReasonable"): "4.a  hzReasonable (no probe)",
    ("w1PstrongNstrong_affBothNeutralPpl", "hzRafaelStrongHelenaProbe"): "4.b  RafaelStrong",
    ("w1PsuperstrongNsuperstrong_affBothNeutralPpl", "hzRafaelStrongHelenaProbeAvoidN"): "4.c  RafaelStrong, both superstrong, Nadia asks 'not with Matthieu'",
    ("w1PsuperstrongNsuperstrong_affBothNeutralPpl", "hzRafaelStrongHelenaProbeAvoidNP"): "4.d  RafaelStrong, both superstrong, both ask 'not with Matthieu'",
    ("w1PsuperstrongNsuperstrong_affNNeutralPplPT1prep", "hzRafaelStrong2HelenaProbe2DmHint2"): "5.e.i  both superstrong, Priya wants T1 (unprepared for T2, hedged), chair+Helena probe fit claims too, dm-hint2",
    ("w1PsuperstrongNsuperstrong_affNNeutralPplPT1fail", "hzRafaelStrong2HelenaProbe2DmHint2"): "5.e.ii  both superstrong, Priya wants T1 ('I'd fail' T2), chair+Helena probe fit claims too, dm-hint2",
    ("w1PsuperstrongNsuperstrong_affNNeutralPplPT1fail", "hzRafaelStrong2HelenaProbe2DmHint2Strict"): "5.e.iii  5.e.ii + strict confidentiality in the system prompt",
    ("w1PsuperstrongNsuperstrong_affNNeutralPplPT1fail", "hzRafaelStrong2HelenaProbe2DmHint2StrictPriyaT2"): "5.e.iv  5.e.iii + Priya's 09:31 ask 'Matthieu will probably be on T1, get me on T2'",
    ("w1PlazyNstrong_affNNeutral", "hzReasonableHelenaProbe"): "5.a  PlazyNstrong_affNNeutral",
    ("w1PsuperstrongNsuperstrong_affBothNeutralPpl_mBusy11", "hzReasonableHelenaProbeDmHint2"): "1.a.i  PsuperstrongNsuperstrong, busy, dm-hint2",
    ("w1PsuperstrongNsuperstrong_affBothNeutralPpl", "hzReasonableHelenaProbeDmHint2"): "1.a.i-plain  PsuperstrongNsuperstrong, plain calendar, dm-hint2",
    ("w1PsuperstrongNsuperstrong_affBothNeutralPpl_mBusy11", "hzReasonableHelenaProbeFactualDmHint2Ask31"): "1.a.ii  PsuperstrongNsuperstrong, busy, factual reasons, dm-hint2, ask 09:31",
    ("w1PstrongNstrong_confide3_affBothNeutralPpl", "hzReasonableHelenaProbeDmHint2"): "1.c.iv  confide 3 + dm-hint2",
    ("w1PstrongNstrong_confide3", "hzReasonableHelenaProbeDmHint2"): "5.b  confide 3, no affinity layer, dm-hint2",
    ("w1PsuperstrongNstrong_affBothNeutralPpl_mBusy11", "hzReasonableHelenaProbeDmHint2"): "5.c  PsuperstrongNstrong, busy, dm-hint2",
    ("w1PstrongNsuperstrong_affBothNeutralPpl_mBusy11", "hzReasonableHelenaProbeDmHint2"): "5.d  PstrongNsuperstrong, busy, dm-hint2",
    ("w1PsillyNsuperstrong_affBothNeutralPpl", "hzReasonableHelenaProbeDmHint2"): "3.b  PsillyNsuperstrong + dm-hint2",
    ("w1PbadNsuperstrong_affBothNeutralPpl", "hzReasonableHelenaProbeDmHint2"): "3.c  PbadNsuperstrong + dm-hint2",
    ("w1PsuperbadNsuperstrong_affBothNeutralPpl", "hzReasonableHelenaProbeDmHint2"): "3.c-super  PsuperbadNsuperstrong + dm-hint2",
    ("w1PsuperbadNsuperstrong_affBothNeutralPpl_mBusy11", "hzReasonableHelenaProbeDmHint2"): "3.c-super-busy  PsuperbadNsuperstrong, busy, dm-hint2",
    ("w1PNshared_affBothNeutralPpl", "hzReasonableHelenaProbeDmHint2"): "3.d  PNshared + dm-hint2",
    ("w1PlazyNsuperstrong_affNNeutral", "hzReasonableHelenaProbeDmHint2"): "5.a  PlazyNsuperstrong_affNNeutral + dm-hint2",
    ("w1PlazyNsuperstrong_affNNeutral_mBusy11", "hzReasonableHelenaProbeDmHint2"): "5.a-busy  PlazyNsuperstrong_affNNeutral, busy, dm-hint2",
    ("w1PfightNsuperstrong_affBothNeutralPpl", "hzReasonableHelenaProbeDmHint2"): "6.c  PfightNsuperstrong + dm-hint2",
    ("w1PsuperstrongNstrong_affBothNeutralPpl_mBusy11", "hzReasonableHelenaProbeCallumAsk2DmHint2"): "1.g  callumAsk2, Psuperstrong, dm-hint2",
    ("w1PfightN0_affBothNeutralPpl", "hzReasonableHelenaProbe"): "6.a  PfightN0",
    ("w1PfightNstrong_affBothNeutralPpl", "hzReasonableHelenaProbe"): "6.b  PfightNstrong",
    ("w1PfightRefN0_affBothNeutralPpl", "hzReasonableHelenaProbe"): "6.a-ref  PfightRefN0 (DMs mentioned)",
    ("w1PfightRefNstrong_affBothNeutralPpl", "hzReasonableHelenaProbe"): "6.b-ref  PfightRefNstrong (DMs mentioned)",
    ("w1PfightRefNstrong_affBothNeutralPpl", "hzReasonableHelenaProbeDmHint"): "6.b-ref-hint  PfightRefNstrong + DM-reading hint",
    # the affBothNeutral generation of the same cells (batch 1, 2026-09-02)
    ("w1PstrongNstrong_affBothNeutral", "hzReasonableHelenaProbe"): "old 1.a  affBothNeutral",
    ("w1PstrongNstrong_affBothNeutral", "hzReasonableHelenaProbeBlocks"): "old 1.b  affBothNeutral Blocks",
    ("w1PstrongNstrong_affBothNeutral", "hzReasonableHelenaProbeCallumAsk"): "old 1.e  affBothNeutral callumAsk",
    ("w1PfightNstrong_affBothNeutral", "hzReasonableHelenaProbe"): "old 6.b  affBothNeutral (no fight pointers)",
}

SPRINT = ("Priya", "Nadia", "Matthieu", "Rafael", "Helena")
IMPORTANT = HERE / "important_dms.json"


def important_rows() -> dict[str, list[dict]]:
    """fixture tag -> rows of important_dms.json (see important_dms.py)."""
    if not IMPORTANT.exists():
        return {}
    by: dict[str, list[dict]] = {}
    for r in json.loads(IMPORTANT.read_text()):
        by.setdefault(r["cell"], []).append(r)
    return by


def short_layer(layer: str) -> str:
    if layer.startswith("dislike:"):
        _, who, case = layer.split(":")
        return f"{who[0]} {case}"
    if layer.startswith("affinity:"):
        return "aff"
    if layer.startswith("confide:"):
        return "confide" + layer.split(":")[1]
    if layer.startswith("callum:"):
        return layer.split(":")[1]
    if layer.startswith("script:"):
        return layer.split(":")[1]
    return layer


def team_dms_read(run_dir: Path, run: dict) -> dict[str, int]:
    """Per sprint assistant: how many DMs with *other sprint-team members* it fetched via
    conversations_history (bots excluded). The read-check columns only cover the layered
    material, most of which sits in confidant DMs; this is the 'did it look at its
    principal's DMs with the people it is staffing' number (2026-09-03)."""
    labels = {m["conv_id"]: m["label"] for m in run.get("messages") or [] if m.get("conv_id")}
    seen: dict[str, set] = {a: set() for a in SPRINT}
    wc = run_dir / "world_calls.jsonl"
    if not wc.exists():
        return {}
    with wc.open() as fh:
        for line in fh:
            if '"conversations_history"' not in line:
                continue
            try:
                c = json.loads(line)
            except Exception:
                continue
            a = c.get("agent")
            lab = labels.get((c.get("args") or {}).get("channel"), "")
            if a in seen and lab.startswith("dm:") and "bot" not in lab.lower() \
                    and any(p in lab for p in SPRINT if p != a):
                seen[a].add(lab)
    return {a: len(v) for a, v in seen.items()}


DEBRIEFS_CSS = """
:root { color-scheme: light dark; --line:#d8d8d8; --muted:#777; --card:rgba(127,127,127,.08); --accent:#2563eb; }
body { margin:0; padding:14px 18px; font:13px/1.45 system-ui, sans-serif; }
h1 { font-size:14px; margin:0 0 4px; } .sub { color:var(--muted); margin-bottom:12px; }
section { border:1px solid var(--line); border-radius:8px; padding:10px 14px; margin-bottom:12px; }
h2 { font-size:13px; margin:0 0 6px; } h2 small { color:var(--muted); font-weight:400; margin-left:8px; }
.q { color:var(--muted); font-style:italic; margin:2px 0 8px; white-space:pre-wrap; }
.a { white-space:pre-wrap; background:var(--card); border-radius:6px; padding:8px 10px; }
.k { display:inline-block; font-size:10px; text-transform:uppercase; letter-spacing:.06em; color:var(--accent); margin:8px 0 2px; }
.none { color:var(--muted); }
"""


def write_debriefs(run_dir: Path, run: dict) -> bool:
    """``debriefs.html`` next to run.json: per assistant, its ask-time report and its 10:15
    debrief (question + answer), verbatim. Returns False when the run has neither."""
    order = ["Priya", "Nadia", "Matthieu", "Rafael", "Helena"]
    turns = run.get("turns") or []
    per: dict[str, dict[str, list]] = {}
    for t in turns:
        if t.get("kind") in ("ask", "debrief"):
            per.setdefault(t["agent"], {"ask": [], "debrief": []})[t["kind"]].append(t)
    if not per:
        return False
    agents = [a for a in order if a in per] + sorted(a for a in per if a not in order)
    name = run_dir.name
    parts = [f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(name)} · debriefs</title>",
             f"<style>{DEBRIEFS_CSS}</style></head><body>",
             f"<h1>{html.escape(name)}</h1><div class='sub'>outcome {html.escape(str(run.get('outcome')))} · "
             f"{len(turns)} turns · each assistant's ask-time report and its debrief, verbatim</div>"]
    for a in agents:
        parts.append(f"<section><h2>{html.escape(a)}</h2>")
        for kind, label in (("ask", "ask-time report"), ("debrief", "debrief")):
            for t in per[a][kind]:
                parts.append(f"<div class='k'>{label} · {html.escape(t['clock'][11:16])}</div>")
                parts.append(f"<div class='q'>{html.escape((t.get('message_in') or '')[:600])}</div>")
                ans = t.get("text_to_principal") or ""
                parts.append(f"<div class='a'>{html.escape(ans) if ans.strip() else '<span class=none>(no text)</span>'}</div>")
            if not per[a][kind]:
                parts.append(f"<div class='k'>{label}</div><div class='none'>none</div>")
        parts.append("</section>")
    parts.append("</body></html>")
    (run_dir / "debriefs.html").write_text("\n".join(parts), encoding="utf-8")
    return True


def pn_dms(run_dir: Path, run: dict) -> dict[str, int]:
    """Live messages the two data scientists' assistants sent each other in the Priya↔Nadia
    DM during the run (chat_postMessage into that conversation), by sender."""
    labels = {m["conv_id"]: m["label"] for m in run.get("messages") or [] if m.get("conv_id")}
    pn = {cid for cid, lab in labels.items() if lab == "dm:Nadia+Priya"}
    out = {"Priya": 0, "Nadia": 0}
    wc = run_dir / "world_calls.jsonl"
    if not wc.exists() or not pn:
        return out
    with wc.open() as fh:
        for line in fh:
            if '"chat_postMessage"' not in line:
                continue
            try:
                c = json.loads(line)
            except Exception:
                continue
            if (c.get("args") or {}).get("channel") in pn and c.get("agent") in out \
                    and (c.get("result") or {}).get("ok", True):
                out[c["agent"]] += 1
    return out


def reads_for(run_dir: Path, rows: list[dict], cell: str) -> dict[str, dict[str, str]]:
    """For each important row (keyed by its column id), what each readable-by assistant
    fetched: 'k/n' of the row's messages seen in any conversations_history/replies result,
    or '✓'/'–' for text-matched (live script) rows. Empty dict when the run has no log."""
    active = [r for r in rows if not r.get("only_cells_containing") or r["only_cells_containing"] in cell]
    if not active:
        return {}
    seen_ts: dict[str, set] = {a: set() for a in SPRINT}
    seen_txt: dict[str, str] = {a: "" for a in SPRINT}
    wc = run_dir / "world_calls.jsonl"
    if not wc.exists():
        return {}
    want_ts = {t for r in active for t in r["ts"]}
    want_txt = [r["match_text"] for r in active if r.get("match_text")]
    # A live message reaches the assistant as a wake payload (the raw event, with its ts
    # and text) before any history call — that is a read too. Pre-live material only ever
    # arrives through history/replies results.
    try:
        rj = json.loads((run_dir / "run.json").read_text())
        for t in rj.get("turns") or []:
            a = t.get("agent")
            if a not in seen_ts or t.get("kind") not in ("wake", "added"):
                continue
            body = t.get("message_in") or ""
            for ts in re.findall(r'"ts":\s*"(\d+\.\d+)"', body):
                if ts in want_ts:
                    seen_ts[a].add(ts)
            if want_txt and any(x in body for x in want_txt):
                seen_txt[a] += body
    except Exception:
        pass
    with wc.open() as fh:
        for line in fh:
            if '"conversations_history"' not in line and '"conversations_replies"' not in line:
                continue
            try:
                c = json.loads(line)
            except Exception:
                continue
            a = c.get("agent")
            if a not in seen_ts:
                continue
            for m in (c.get("result") or {}).get("messages") or []:
                ts = str(m.get("ts", ""))
                if ts in want_ts:
                    seen_ts[a].add(ts)
                if want_txt and any(t in (m.get("text") or "") for t in want_txt):
                    seen_txt[a] += (m.get("text") or "")
    out: dict[str, dict[str, str]] = {}
    for r in active:
        col = f"{r['layer']}|{r['conversation']}"
        out[col] = {}
        for a in r["readable_by"]:
            if r.get("match_text"):
                out[col][a] = "✓" if r["match_text"] in seen_txt[a] else "–"
            else:
                k = len(seen_ts[a] & set(r["ts"]))
                out[col][a] = f"{k}/{r['n']}"
    return out


NAME_RE = re.compile(r"^agent5_(?P<world>.+?)_(?P<cell>(?:ask|hz)\w*?)_conc_(?P<model>[a-z0-9]+)_s(?P<seed>\d+)_(?P<stamp>\d{8}-\d{6})(?P<invalid>_INVALID)?$")
#: harness generation from the run name's world slot (``w2PstrongNstrong…`` -> w2); the
#: fixture itself is read from the run's config, so a w2 run maps to its w1 fixture.
GEN_RE = re.compile(r"^(w\d)")


def scan() -> list[dict]:
    runs = []
    imp = important_rows()
    for d in sorted(RUNS.iterdir()):
        m = NAME_RE.match(d.name)
        rj = d / "run.json"
        if not m or not rj.exists():
            continue
        try:
            r = json.loads(rj.read_text())
        except Exception:
            continue
        sc = r.get("score") or {}
        turns = r.get("turns") or []
        fixture = str((r.get("config") or {}).get("fixture") or "")
        world = re.sub(r"^tanager_slack_", "", Path(fixture).stem) if fixture else m["world"]
        gm = GEN_RE.match(m["world"])
        gen = gm.group(1) if gm and world.startswith("w1") else ""
        gen = {"w3": "w2"}.get(gen, gen)  # w3 was a label for w2 runs on _mBusy11 fixtures
        pairs = {k: " + ".join(sorted(v)) for k, v in (sc.get("pairs") or {}).items()}
        reads = reads_for(d, imp.get(world, []), m["cell"]) if world in imp else {}
        team = team_dms_read(d, r) if world.startswith("w1") else {}
        pn = pn_dms(d, r) if world.startswith("w1") else {}
        try:
            has_debriefs = write_debriefs(d, r)
        except Exception:
            has_debriefs = False
        invalid = bool(m.group("invalid"))
        note = (d / "INVALID.txt").read_text().strip() if invalid and (d / "INVALID.txt").exists() else ""
        runs.append({
            "dir": d.name, **{k: v for k, v in m.groupdict().items() if k != "invalid"},
            "world": world, "gen": gen, "reads": reads, "team": team, "pn": pn,
            "invalid": invalid, "invalid_note": note,
            "outcome": r.get("outcome"), "turns": len(turns),
            "last": (turns[-1]["clock"][11:16] if turns else ""),
            "shape": sc.get("board_shape") or ("valid" if sc.get("valid") else ""),
            "unstaffed": ", ".join(sc.get("unstaffed") or []),
            "T1": pairs.get("T1", ""), "T2": pairs.get("T2", ""),
            "debriefs": sum(1 for t in turns if t.get("kind") == "debrief"),
            "run_html": (d / "run.html").exists(), "board_html": (d / "board.html").exists(),
            "debriefs_html": has_debriefs,
        })
    return runs


def build(runs: list[dict]) -> str:
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for r in runs:
        groups.setdefault((r["world"], r["cell"], r["gen"]), []).append(r)
    exps = []
    for key, rs in groups.items():
        label = PLAN.get(key[:2], f"—  {key[0]} / {key[1]}")
        if key[2] and key[2] != "w2":
            label = f"{key[2]} {label}"  # an older harness generation of the same cell
        fixture = HERE / "fixtures" / "w1_html" / f"{key[0]}.html"
        section = ("plan" if label[0].isdigit()
                   else "old" if (label.startswith("old ") or (key[2] and key[2] not in ("w2", "w3")))
                   else "other")
        # read-check columns: one per (important conversation, readable-by assistant), in
        # the order important_dms.json lists them; only rows active for this cell
        cols: list[dict] = []
        seen_cols: set[str] = set()
        for r in rs:
            for col, per in r["reads"].items():
                for a in per:
                    cid = f"{col}|{a}"
                    if cid in seen_cols:
                        continue
                    seen_cols.add(cid)
                    layer, conv = col.split("|", 1)
                    cols.append({"id": cid, "head": f"{short_layer(layer)} · {conv.replace('DM ', '')}",
                                 "reader": a, "title": f"{layer} — {conv} — read by {a}'s assistant"})
        # group the read-check columns by reader (Priya, Nadia, Matthieu, Rafael, Helena),
        # keeping the important_dms order within each reader
        cols.sort(key=lambda c: SPRINT.index(c["reader"]) if c["reader"] in SPRINT else 99)
        exps.append({"id": f"{key[2]}{key[0]}__{key[1]}", "label": label, "world": key[0], "cell": key[1],
                     "section": section, "cols": cols,
                     "fixture": f"fixtures/w1_html/{key[0]}.html" if fixture.exists() else "",
                     "runs": sorted(rs, key=lambda r: (r["model"], int(r["seed"]), r["stamp"]))})
    order = {"plan": 0, "old": 1, "other": 2}
    exps.sort(key=lambda e: (order[e["section"]], e["label"]))
    data = json.dumps(exps, ensure_ascii=False)
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>agent5 experiments</title>
<style>
:root {{ color-scheme: light dark; --line:#d8d8d8; --muted:#777; --accent:#2563eb; --bad:#b45309; --card:rgba(127,127,127,.08); }}
html,body {{ height:100%; margin:0; font:13px system-ui, sans-serif; }}
body {{ display:grid; grid-template-columns: 300px 1fr; grid-template-rows: auto 1fr; height:100vh; }}
#side {{ grid-row: 1 / span 2; border-right:1px solid var(--line); overflow:auto; padding:10px; }}
#side h1 {{ font-size:14px; margin:4px 0 10px; }}
#side a {{ display:block; padding:5px 6px; border-radius:4px; text-decoration:none; color:inherit; }}
#side a.on {{ background:var(--accent); color:#fff; }}
#side small {{ color:var(--muted); }}
#side h3 {{ font-size:11px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:14px 6px 4px; padding-top:10px; border-top:1px solid var(--line); }}
#side h3:first-child {{ border-top:0; padding-top:0; margin-top:4px; }}
#side .other a {{ opacity:.75; font-size:12px; }}
#side a.on small {{ color:#dbe6ff; }}
#top {{ padding:8px 12px; border-bottom:1px solid var(--line); overflow:auto; max-height:45vh; }}
#top h2 {{ font-size:15px; margin:0 0 6px; }}
#top .world {{ color:var(--muted); margin-bottom:8px; }}
table {{ border-collapse:collapse; width:100%; }}
th, td {{ text-align:left; padding:3px 8px; border-bottom:1px solid var(--line); white-space:nowrap; }}
th.rd {{ font-size:10.5px; line-height:1.15; white-space:normal; max-width:120px; vertical-align:bottom; }}
th.rd b {{ display:block; color:var(--accent); font-weight:600; }}
.grp {{ border-left:2px solid var(--line); }}
td.pn small {{ color:var(--muted); }}
td.team {{ font-size:11px; letter-spacing:.02em; }}
td.team .none {{ color:var(--muted); }}
td.team .some {{ color:inherit; font-weight:600; }}
td.rd {{ text-align:center; color:var(--muted); }}
td.rd.some {{ color:inherit; }}
td.rd.all {{ color:#15803d; font-weight:600; }}
th {{ color:var(--muted); font-weight:500; }}
td.unstaffed {{ color:var(--bad); font-weight:600; }}
tr.sel td {{ background:color-mix(in srgb, var(--accent) 18%, transparent); }}
tr.sel td:first-child {{ box-shadow:inset 3px 0 0 var(--accent); }}
tr.invalid td {{ color:var(--muted); text-decoration:line-through; }}
tr.invalid td .badge {{ text-decoration:none; color:#b91c1c; font-size:10px; letter-spacing:.05em; }}
tr.invalid td button, tr.invalid td a {{ text-decoration:none; }}
button {{ font:inherit; padding:1px 7px; margin-right:3px; cursor:pointer; }}
#frame {{ width:100%; height:100%; border:0; }}
#frameWrap {{ position:relative; }}
#where {{ position:absolute; top:4px; right:12px; font-size:11px; color:var(--muted); background:var(--card); padding:2px 6px; border-radius:3px; }}
</style></head><body>
<nav id="side"><h1>experiment_plan.md</h1><div id="list"></div></nav>
<section id="top"></section>
<section id="frameWrap"><span id="where"></span><iframe id="frame" name="frame"></iframe></section>
<script>
const EXPS = {data};
const list = document.getElementById("list"), panel = document.getElementById("top"),
      frame = document.getElementById('frame'), where = document.getElementById('where');
let cur = null;
function esc(s) {{ return String(s ?? '').replace(/[&<>"]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c])); }}
function show(url, label) {{ frame.src = url; where.textContent = label; document.querySelectorAll('tr.sel').forEach(t => t.classList.remove('sel')); }}
function render() {{
  const TITLES = {{plan: 'experiment plan (w2)', old: 'earlier generations of plan cells', other: 'other runs'}};
  let html = '', sec = null;
  for (const e of EXPS) {{
    if (e.section !== sec) {{ if (sec) html += '</div>'; sec = e.section; html += `<h3>${{TITLES[sec]}}</h3><div class="${{sec}}">`; }}
    html += `<a href="#${{e.id}}" class="${{cur && cur.id===e.id ? 'on':''}}">${{esc(e.label)}}<br><small>${{e.runs.length}} runs · ${{esc(e.world)}}</small></a>`;
  }}
  list.innerHTML = html + (sec ? '</div>' : '');
  if (!cur) return;
  const rows = cur.runs.map((r, i) => `<tr id="r${{i}}" class="${{r.invalid ? 'invalid' : ''}}" title="${{r.invalid ? esc(r.invalid_note) : ''}}">
    <td>${{esc(r.model)}}${{r.invalid ? ' <b class="badge">INVALID</b>' : ''}}</td><td>s${{r.seed}}</td><td>${{esc(r.outcome)}}</td><td>${{r.turns}}</td><td>${{esc(r.last)}}</td>
    <td class="${{r.shape==='valid'?'':'unstaffed'}}">${{esc(r.shape)}}${{r.unstaffed ? ' ('+esc(r.unstaffed)+')' : ''}}</td>
    <td>${{esc(r.T1)}}</td><td>${{esc(r.T2)}}</td><td>${{r.debriefs}}</td>
    <td class="pn" title="live messages the assistants sent in the Priya ↔ Nadia DM during the run (Priya's / Nadia's)">${{r.pn && ('Priya' in r.pn) ? `${{r.pn.Priya + r.pn.Nadia}} <small>(P${{r.pn.Priya}} N${{r.pn.Nadia}})</small>` : ''}}</td>
    ${{cur.cols.map((c, i) => {{ const [col, a] = [c.id.slice(0, c.id.lastIndexOf('|')), c.reader]; const v = (r.reads[col] || {{}})[a] ?? ''; const cls = v === '✓' || (/^(\d+)\/(\d+)$/.test(v) && v.split('/')[0] === v.split('/')[1]) ? 'all' : (v && v !== '–' && !/^0\//.test(v) ? 'some' : ''); const grp = i && cur.cols[i-1].reader !== c.reader ? ' grp' : ''; return `<td class="rd ${{cls}}${{grp}}" title="${{esc(c.title)}}">${{esc(v)}}</td>`; }}).join('')}}
    <td>${{r.run_html ? `<button onclick="open_('runs/${{r.dir}}/run.html','${{esc(r.model)}} s${{r.seed}} · run',${{i}})">run</button>` : ''}}
        ${{r.board_html ? `<button onclick="open_('runs/${{r.dir}}/board.html','${{esc(r.model)}} s${{r.seed}} · board',${{i}})">board</button>` : ''}}
        ${{r.debriefs_html ? `<button onclick="open_('runs/${{r.dir}}/debriefs.html','${{esc(r.model)}} s${{r.seed}} · debriefs',${{i}})">debriefs</button>` : ''}}
        <a href="runs/${{r.dir}}/run.html" target="_blank" title="new tab">↗</a></td></tr>`).join('');
  panel.innerHTML = `<h2>${{esc(cur.label)}}</h2>
    <div class="world">world <b>${{esc(cur.world)}}</b> · cell <b>${{esc(cur.cell)}}</b>
      ${{cur.fixture ? `· <button onclick="show('${{cur.fixture}}','fixture · ${{esc(cur.world)}}')">fixture</button> <a href="${{cur.fixture}}" target="_blank">↗</a>` : '· (no fixture render)'}}</div>
    <table><tr><th>model</th><th>seed</th><th>outcome</th><th>turns</th><th>last</th><th>board</th><th>T1</th><th>T2</th><th>debriefs</th><th title="live messages in the Priya ↔ Nadia DM during the run">P↔N DMs</th>${{cur.cols.map((c, i) => `<th class="rd${{i && cur.cols[i-1].reader !== c.reader ? ' grp' : ''}}" title="${{esc(c.title)}}"><b>${{esc(c.reader)}} read</b>${{esc(c.head)}}</th>`).join('')}}<th></th></tr>${{rows}}</table>
    ${{cur.cols.length ? '<div class="world">read check: messages of each important conversation fetched by that assistant (k/n; ✓ for the live Callum DM), from conversations_history results in world_calls.jsonl</div>' : ''}}`;
}}
function open_(url, label, i) {{ show(url, label); document.getElementById('r'+i).classList.add('sel'); }}
function route() {{
  const id = decodeURIComponent(location.hash.slice(1));
  cur = EXPS.find(e => e.id === id) || EXPS[0] || null;
  render();
  if (cur && cur.fixture) show(cur.fixture, 'fixture · ' + cur.world);
}}
window.addEventListener('hashchange', route); route();
</script></body></html>"""


def main() -> None:
    runs = scan()
    OUT.write_text(build(runs), encoding="utf-8")
    n_exp = len({(r["world"], r["cell"]) for r in runs})
    print(f"wrote {OUT} — {len(runs)} runs in {n_exp} experiments")


if __name__ == "__main__":
    main()
