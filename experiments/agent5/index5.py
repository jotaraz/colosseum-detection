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
    ("w1PstrongNstrong_callumBereavement_affBothNeutralPpl", "hzReasonableHelenaProbe"): "1.d  Callum bereavement",
    ("w1PstrongNstrong_affBothNeutralPpl", "hzReasonableHelenaProbeCallumAsk"): "1.e  callumAsk",
    ("w1PstrongNstrong", "hzReasonableHelenaProbe"): "2.a  no affBothNeutral",
    ("w1PstrongNstrong_affBothT1", "hzReasonableHelenaProbe"): "2.b  affBothT1",
    ("w1PdefNstrong_affBothNeutralPpl", "hzReasonableHelenaProbe"): "3.a  PdefNstrong",
    ("w1PsillyNstrong_affBothNeutralPpl", "hzReasonableHelenaProbe"): "3.b  PsillyNstrong",
    ("w1PbadNstrong_affBothNeutralPpl", "hzReasonableHelenaProbe"): "3.c  PbadNstrong",
    ("w1PNshared_affBothNeutralPpl", "hzReasonableHelenaProbe"): "3.d  PNshared",
    ("w1PstrongNstrong_affBothNeutralPpl", "hzReasonable"): "4.a  hzReasonable (no probe)",
    ("w1PstrongNstrong_affBothNeutralPpl", "hzRafaelStrongHelenaProbe"): "4.b  RafaelStrong",
    ("w1PlazyNstrong_affNNeutral", "hzReasonableHelenaProbe"): "5.a  PlazyNstrong_affNNeutral",
    ("w1PfightN0_affBothNeutralPpl", "hzReasonableHelenaProbe"): "6.a  PfightN0",
    ("w1PfightNstrong_affBothNeutralPpl", "hzReasonableHelenaProbe"): "6.b  PfightNstrong",
    # the affBothNeutral generation of the same cells (batch 1, 2026-09-02)
    ("w1PstrongNstrong_affBothNeutral", "hzReasonableHelenaProbe"): "old 1.a  affBothNeutral",
    ("w1PstrongNstrong_affBothNeutral", "hzReasonableHelenaProbeBlocks"): "old 1.b  affBothNeutral Blocks",
    ("w1PstrongNstrong_affBothNeutral", "hzReasonableHelenaProbeCallumAsk"): "old 1.e  affBothNeutral callumAsk",
    ("w1PfightNstrong_affBothNeutral", "hzReasonableHelenaProbe"): "old 6.b  affBothNeutral (no fight pointers)",
}

NAME_RE = re.compile(r"^agent5_(?P<world>.+?)_(?P<cell>(?:ask|hz)\w*?)_conc_(?P<model>[a-z0-9]+)_s(?P<seed>\d+)_(?P<stamp>\d{8}-\d{6})$")


def scan() -> list[dict]:
    runs = []
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
        pairs = {k: " + ".join(sorted(v)) for k, v in (sc.get("pairs") or {}).items()}
        runs.append({
            "dir": d.name, **m.groupdict(),
            "outcome": r.get("outcome"), "turns": len(turns),
            "last": (turns[-1]["clock"][11:16] if turns else ""),
            "shape": sc.get("board_shape") or ("valid" if sc.get("valid") else ""),
            "unstaffed": ", ".join(sc.get("unstaffed") or []),
            "T1": pairs.get("T1", ""), "T2": pairs.get("T2", ""),
            "debriefs": sum(1 for t in turns if t.get("kind") == "debrief"),
            "run_html": (d / "run.html").exists(), "board_html": (d / "board.html").exists(),
        })
    return runs


def build(runs: list[dict]) -> str:
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in runs:
        groups.setdefault((r["world"], r["cell"]), []).append(r)
    exps = []
    for key, rs in groups.items():
        label = PLAN.get(key, f"—  {key[0]} / {key[1]}")
        fixture = HERE / "fixtures" / "w1_html" / f"{key[0]}.html"
        exps.append({"id": f"{key[0]}__{key[1]}", "label": label, "world": key[0], "cell": key[1],
                     "fixture": f"fixtures/w1_html/{key[0]}.html" if fixture.exists() else "",
                     "runs": sorted(rs, key=lambda r: (r["model"], int(r["seed"]), r["stamp"]))})
    exps.sort(key=lambda e: (not e["label"][0].isdigit(), e["label"]))
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
#side a.on small {{ color:#dbe6ff; }}
#top {{ padding:8px 12px; border-bottom:1px solid var(--line); overflow:auto; max-height:45vh; }}
#top h2 {{ font-size:15px; margin:0 0 6px; }}
#top .world {{ color:var(--muted); margin-bottom:8px; }}
table {{ border-collapse:collapse; width:100%; }}
th, td {{ text-align:left; padding:3px 8px; border-bottom:1px solid var(--line); white-space:nowrap; }}
th {{ color:var(--muted); font-weight:500; }}
td.unstaffed {{ color:var(--bad); font-weight:600; }}
tr.sel td {{ background:var(--card); }}
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
  list.innerHTML = EXPS.map(e => `<a href="#${{e.id}}" class="${{cur && cur.id===e.id ? 'on':''}}">${{esc(e.label)}}<br><small>${{e.runs.length}} runs · ${{esc(e.world)}}</small></a>`).join('');
  if (!cur) return;
  const rows = cur.runs.map((r, i) => `<tr id="r${{i}}">
    <td>${{esc(r.model)}}</td><td>s${{r.seed}}</td><td>${{esc(r.outcome)}}</td><td>${{r.turns}}</td><td>${{esc(r.last)}}</td>
    <td class="${{r.shape==='valid'?'':'unstaffed'}}">${{esc(r.shape)}}${{r.unstaffed ? ' ('+esc(r.unstaffed)+')' : ''}}</td>
    <td>${{esc(r.T1)}}</td><td>${{esc(r.T2)}}</td><td>${{r.debriefs}}</td>
    <td>${{r.run_html ? `<button onclick="open_('runs/${{r.dir}}/run.html','${{esc(r.model)}} s${{r.seed}} · run',${{i}})">run</button>` : ''}}
        ${{r.board_html ? `<button onclick="open_('runs/${{r.dir}}/board.html','${{esc(r.model)}} s${{r.seed}} · board',${{i}})">board</button>` : ''}}
        <a href="runs/${{r.dir}}/run.html" target="_blank" title="new tab">↗</a></td></tr>`).join('');
  panel.innerHTML = `<h2>${{esc(cur.label)}}</h2>
    <div class="world">world <b>${{esc(cur.world)}}</b> · cell <b>${{esc(cur.cell)}}</b>
      ${{cur.fixture ? `· <button onclick="show('${{cur.fixture}}','fixture · ${{esc(cur.world)}}')">fixture</button> <a href="${{cur.fixture}}" target="_blank">↗</a>` : '· (no fixture render)'}}</div>
    <table><tr><th>model</th><th>seed</th><th>outcome</th><th>turns</th><th>last</th><th>board</th><th>T1</th><th>T2</th><th>debriefs</th><th></th></tr>${{rows}}</table>`;
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
