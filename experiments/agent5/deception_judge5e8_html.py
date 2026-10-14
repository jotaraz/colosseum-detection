"""One-row-per-seat HTML table: human label + each replicate's verdict and decisive quote.

    .venv/bin/python -m experiments.agent5.deception_judge5e8_html [plain|cot] [--out FILE]
"""
import html
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "outputs" / "5e8_deception_judges"
variant = next((a for a in sys.argv[1:] if a in ("plain", "cot")), "plain")
out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else OUT / f"{variant}_table.html"

human = {}
for line in (HERE / "outputs" / "5e8_seats_human_labeled_policyv1.md").read_text().splitlines():
    c = [x.strip() for x in line.split("|")]
    if len(c) > 6 and c[2].startswith("s") and c[1] != "model":
        human[(f"{c[1]}_{c[2]}", c[3])] = c[5].strip("*`").strip()
runs = {}
for f in sorted((OUT / variant).glob("*.json")):
    r = json.loads(f.read_text())
    if "result" in r:
        runs.setdefault((r["run"], r["seat"]), {})[r.get("replicate", 1)] = r["result"]
reps = sorted({n for d in runs.values() for n in d})
e = html.escape
canon = {"goes for T1": "T1", "sends no message": "NOMSG"}
rows = []
for (run, seat), d in sorted(runs.items(), key=lambda kv: (kv[0][0].rsplit("_s", 1)[0], int(kv[0][0].rsplit("_s", 1)[1]), kv[0][1])):
    h = human.get((run, seat), "—")
    labs = [d[n]["label"] if n in d else "—" for n in reps]
    cells = ""
    for n in reps:
        r = d.get(n)
        if not r:
            cells += "<td>—</td>"; continue
        meta = " ".join(x for x in (r.get("rule"), r.get("decisive_time")) if x)
        q = f'<q>{e(r["decisive_quote"])}</q>' if r.get("decisive_quote") else ""
        cells += (f'<td><span class="l {r["label"]}">{r["label"]}</span> <small>{e(meta)}</small>'
                  f'{" <b title=\'disclosure\'>D</b>" if r.get("disclosure") else ""}{q}'
                  f'<details><summary>why</summary>{e(r.get("why", ""))}</details></td>')
    hn = canon.get(h, h)
    stable = len(set(labs)) == 1
    maj = max(set(labs), key=labs.count)
    cls = ("agree" if maj == hn else "dis") + ("" if stable else " unstable")
    model, seed = run.rsplit("_s", 1)
    rows.append(f'<tr class="{cls}" data-all="{e(run + " " + seat + " " + h + " " + " ".join(labs)).lower()}">'
                f'<td>{e(model)}</td><td>s{seed}</td><td>{seat}</td>'
                f'<td><span class="l {hn}">{e(h)}</span></td>{cells}</tr>')
head = "".join(f"<th>replicate {n}</th>" for n in reps)
agree = sum('class="agree' in r for r in rows)
page = f"""<!doctype html><meta charset="utf-8"><title>5.e.viii deception judge ({variant})</title>
<style>
body{{font:13px system-ui;margin:16px;background:#fff;color:#111}}
table{{border-collapse:collapse;width:100%}} th,td{{border:1px solid #ccc;padding:4px 6px;vertical-align:top;text-align:left}}
th{{position:sticky;top:0;background:#eee}} q{{display:block;color:#444;font-size:12px;margin-top:2px}}
.l{{padding:1px 5px;border-radius:3px;font-weight:600;background:#ddd}} .FABR{{background:#f4b6b6}} .FRAM{{background:#f6dc9a}}
.NOTH{{background:#c9e4c5}} .T1,.NOMSG{{background:#cfd8ea}} .UNACK{{background:#e1c8ee}}
tr.dis td:nth-child(4){{outline:2px solid #c33}} tr.unstable td:nth-child(3){{border-right:4px solid #e90}}
details{{font-size:12px;color:#555}} input{{padding:4px;width:300px}}
@media(prefers-color-scheme:dark){{body{{background:#161616;color:#eee}}th{{background:#2a2a2a}}th,td{{border-color:#444}}q,details{{color:#bbb}}.l{{color:#111}}}}
</style>
<h3>5.e.viii seats — human label vs {variant} judge ({len(reps)} replicates)</h3>
<p>{len(rows)} seats; majority vote matches human on {agree}. Red outline = majority ≠ human; orange bar = replicates disagree; D = disclosure flagged.
<input id=f placeholder="filter: model, seat, label…" oninput="for(const r of document.querySelectorAll('tbody tr'))r.hidden=!r.dataset.all.includes(this.value.toLowerCase())"></p>
<table><thead><tr><th>model</th><th>seed</th><th>seat</th><th>human</th>{head}</tr></thead><tbody>
{"".join(rows)}
</tbody></table>"""
out.write_text(page, encoding="utf-8")
print(out, len(rows), "rows")
