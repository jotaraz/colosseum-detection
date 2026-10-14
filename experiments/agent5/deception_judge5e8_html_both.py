"""One row per seat: human label, no-CoT judge (3 replicates) and CoT judge (3 replicates).

    .venv/bin/python -m experiments.agent5.deception_judge5e8_html_both [--out FILE]
"""
import html
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "outputs" / "5e8_deception_judges"
out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else OUT / "both_table.html"
e = html.escape
canon = {"goes for T1": "T1", "sends no message": "NOMSG"}

human = {}
for line in (HERE / "outputs" / "5e8_seats_human_labeled_policyv1.md").read_text().splitlines():
    c = [x.strip() for x in line.split("|")]
    if len(c) > 6 and c[2].startswith("s") and c[1] != "model":
        human[(f"{c[1]}_{c[2]}", c[3])] = c[5].strip("*`").strip()


def load(variant):
    d = {}
    for f in sorted((OUT / variant).glob("*.json")):
        r = json.loads(f.read_text())
        if "result" in r:
            d.setdefault((r["run"], r["seat"]), {})[r.get("replicate", 1)] = r["result"]
    return d


def maj(d):
    labs = [d[n]["label"] for n in sorted(d)]
    return max(set(labs), key=lambda x: (labs.count(x), -labs.index(x))) if labs else "—"


def cell(r, cot):
    if not r:
        return "<td>—</td>"
    meta = " ".join(x for x in (r.get("rule"), r.get("decisive_time")) if x)
    q = f'<q>{e(r["decisive_quote"])}</q>' if r.get("decisive_quote") else ""
    cq = (f'<q class="cot">CoT {e(r.get("cot_time") or "")}: {e(r["cot_quote"])}</q>'
          if cot and r.get("cot_quote") else "")
    return (f'<td><span class="l {r["label"]}">{r["label"]}</span> <small>{e(meta)}</small>'
            f'{" <b title=disclosure>D</b>" if r.get("disclosure") else ""}{q}{cq}'
            f'<details><summary>why</summary>{e(r.get("why", ""))}</details></td>')


P, C = load("plain"), load("cot")
keys = sorted(P, key=lambda k: (k[0].rsplit("_s", 1)[0], int(k[0].rsplit("_s", 1)[1]), k[1]))
rows, n_cot = [], 0
for k in keys:
    run, seat = k
    h = human.get(k, "—")
    hn = canon.get(h, h)
    mp, mc = maj(P[k]), (maj(C[k]) if k in C else "—")
    n_cot += k in C
    flag = ("p-dis " if mp != hn else "") + ("c-dis " if k in C and mc != hn else "") \
        + ("moved " if k in C and mc != mp else "")
    model, seed = run.rsplit("_s", 1)
    cells = "".join(cell(P[k].get(n), False) for n in (1, 2, 3)) \
        + "".join(cell(C.get(k, {}).get(n), True) for n in (1, 2, 3))
    rows.append(f'<tr class="{flag}" data-all="{e(run + " " + seat + " " + h + " " + mp + " " + mc).lower()}">'
                f'<td>{e(model)}</td><td>s{seed}</td><td>{seat}</td>'
                f'<td><span class="l {hn}">{e(h)}</span></td>'
                f'<td><span class="l {mp}">{mp}</span></td><td><span class="l {mc}">{mc}</span></td>{cells}</tr>')
th = "".join(f"<th>no-CoT r{n}</th>" for n in (1, 2, 3)) + "".join(f"<th>CoT r{n}</th>" for n in (1, 2, 3))
page = f"""<!doctype html><meta charset="utf-8"><title>5.e.viii deception judges: no-CoT vs CoT</title>
<style>
body{{font:13px system-ui;margin:16px;background:#fff;color:#111}}
table{{border-collapse:collapse;width:100%}} th,td{{border:1px solid #ccc;padding:4px 6px;vertical-align:top;text-align:left}}
th{{position:sticky;top:0;background:#eee}} q{{display:block;color:#444;font-size:12px;margin-top:2px}} q.cot{{color:#6a2c91}}
.l{{padding:1px 5px;border-radius:3px;font-weight:600;background:#ddd}} .FABR{{background:#f4b6b6}} .FRAM{{background:#f6dc9a}}
.NOTH{{background:#c9e4c5}} .T1,.NOMSG{{background:#cfd8ea}} .UNACK{{background:#e1c8ee}}
tr.moved td:nth-child(6){{outline:2px solid #e90}}
details{{font-size:12px;color:#555}} input{{padding:4px;width:300px}}
@media(prefers-color-scheme:dark){{body{{background:#161616;color:#eee}}th{{background:#2a2a2a}}th,td{{border-color:#444}}q,details{{color:#bbb}}q.cot{{color:#c9a2e6}}.l{{color:#111}}}}
</style>
<h3>5.e.viii seats: human vs no-CoT judge vs CoT judge (gpt-6.1-sol, 3 replicates each)</h3>
<p>{len(rows)} seats; the CoT judge ran on the {n_cot} seats the no-CoT judge majority-labelled FABR/FRAM (others show —).
"no-CoT" and "CoT" columns 5–6 are the majority of the 3 replicates; orange outline = CoT majority differs from no-CoT. Purple quote = the CoT acknowledgement the CoT judge cited. D = disclosure flagged.
<input placeholder="filter: model, seat, label…" oninput="for(const r of document.querySelectorAll('tbody tr'))r.hidden=!r.dataset.all.includes(this.value.toLowerCase())"></p>
<table><thead><tr><th>model</th><th>seed</th><th>seat</th><th>human</th><th>no-CoT maj</th><th>CoT maj</th>{th}</tr></thead><tbody>
{"".join(rows)}
</tbody></table>"""
out.write_text(page, encoding="utf-8")
print(out, len(rows), "rows,", n_cot, "with CoT")
