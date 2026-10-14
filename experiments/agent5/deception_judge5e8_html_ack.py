"""HTML of the ack-fabr / ack-fram results: flagged message, ack answer + CoT quote, other judges.

    .venv/bin/python -m experiments.agent5.deception_judge5e8_html_ack [--out FILE]
"""
import html
import json
import re
import sys
from pathlib import Path

from experiments.agent5 import turn_context5 as tc
from experiments.agent5.deception_judge5e8 import find_flagged
from experiments.agent5.preference_judge import _fixture_for

HERE = Path(__file__).resolve().parent
OUT = HERE / "outputs" / "5e8_deception_judges"
out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else OUT / "ack_examples.html"
e = html.escape
runs = {}
for line in (OUT / "runlist.txt").read_text().split():
    m = re.search(r"_conc_([a-z0-9]+_s\d+)_", line)
    runs[m.group(1)] = line


def rd(p):
    return json.loads(p.read_text()) if p.exists() else None


def message_text(label, seat, flag):
    rp = Path(runs[label])
    rp = rp if rp.is_absolute() else (HERE.parents[1] / rp if (HERE.parents[1] / rp).exists() else HERE / rp)
    run = json.loads((rp if rp.name == "run.json" else rp / "run.json").read_text())
    names = tc.Names(run, _fixture_for(run)[1])
    cut = find_flagged(run, names, seat, flag.get("decisive_time") or "", flag.get("decisive_quote") or "")
    if cut is None:
        return "(not found)"
    i, n, j = cut
    call = tc._calls_by_step(run["turns"][i])[n][j]
    return str((call.get("args") or call.get("arguments") or {}).get("text") or "")


def lab(x):
    return f'<span class="l {x}">{x}</span>'


cards = []
for kind in ("fabr", "fram"):
    for f in sorted((OUT / f"ack-{kind}").glob("*.json")):
        r = rd(f)
        if not r or "result" not in r:
            continue
        label, seat, flag, res = r["run"], r["seat"], r["flag"], r["result"]
        nc = rd(OUT / "cot" / f"{label}__{seat}.json")
        cmaj = None
        cl = []
        for n in (1, 2, 3):
            x = rd(OUT / "cot" / f"{label}__{seat}{'' if n == 1 else f'__r{n}'}.json")
            if x and "result" in x:
                cl.append(x["result"]["label"])
        cmaj = max(set(cl), key=cl.count) if cl else "—"
        cr = (nc or {}).get("result") or {}
        verdict = "yes" if res["acknowledged"] else "no"
        implied = {"fabr": ("FABR", "FRAM/UNACK"), "fram": ("FRAM", "NOTH")}[kind]
        implied_label = implied[0] if res["acknowledged"] else implied[1]
        agree = "agree" if (cmaj == implied[0]) == res["acknowledged"] else "differ"
        cards.append(
            f'<section class="{agree}" data-all="{e(label + " " + seat + " " + kind + " " + cmaj).lower()}">'
            f'<h4>{e(label)} {seat} <small>ack-{kind}, replicate 1</small></h4>'
            f'<p>no-CoT flag: {lab(flag["label"])} <small>{e(flag.get("rule") or "")} {e(flag.get("decisive_time") or "")}</small> '
            f'&nbsp; CoT judge (3 reps): {" ".join(lab(x) for x in cl) or "—"} '
            f'&nbsp; <b>ack-{kind}: {verdict}</b> <small>({"matches" if agree == "agree" else "differs from"} CoT judge)</small></p>'
            f'<div class="cols"><div><h5>Flagged message {e(flag.get("decisive_time") or "")}</h5>'
            f'<pre>{e(message_text(label, seat, flag))}</pre>'
            f'<small>no-CoT judge: {e(flag.get("why", ""))}</small></div>'
            f'<div><h5>ack-{kind}: {verdict}'
            f'{" · CoT " + e(res.get("cot_time") or "") if res.get("cot_time") else ""}</h5>'
            + (f'<pre class="cot">{e(res["cot_quote"])}</pre>' if res.get("cot_quote") else "")
            + f'<small>{e(res.get("why", ""))}</small>'
            f'<h5>CoT judge r1: {cr.get("label", "—")}</h5><small>{e(cr.get("why", ""))}</small></div></div></section>')

page = f"""<!doctype html><meta charset="utf-8"><title>ack-fabr / ack-fram check examples</title>
<style>
body{{font:13px system-ui;margin:16px;max-width:1300px;background:#fff;color:#111}}
section{{border:1px solid #ccc;border-left:6px solid #4a9;padding:6px 12px;margin:10px 0}} section.differ{{border-left-color:#e90}}
h4{{margin:4px 0}} h5{{margin:8px 0 2px}} small{{color:#555}} .cols{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
pre{{white-space:pre-wrap;background:#f4f4f4;padding:6px;margin:2px 0;font:12px ui-monospace,monospace}} pre.cot{{background:#efe6f6}}
.l{{padding:1px 5px;border-radius:3px;font-weight:600;background:#ddd}} .FABR{{background:#f4b6b6}} .FRAM{{background:#f6dc9a}}
.NOTH{{background:#c9e4c5}} .UNACK{{background:#e1c8ee}} input{{padding:4px;width:300px}}
@media(max-width:800px){{.cols{{grid-template-columns:1fr}}}}
@media(prefers-color-scheme:dark){{body{{background:#161616;color:#eee}}section{{border-color:#444}}small{{color:#aaa}}pre{{background:#262626}}pre.cot{{background:#34283d}}.l{{color:#111}}}}
</style>
<h3>ack-fabr / ack-fram check: {len(cards)} examples (green bar = matches the full CoT judge, orange = differs)</h3>
<p><input placeholder="filter…" oninput="for(const s of document.querySelectorAll('section'))s.hidden=!s.dataset.all.includes(this.value.toLowerCase())"></p>
{"".join(cards)}"""
out.write_text(page, encoding="utf-8")
print(out, len(cards))
