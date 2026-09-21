"""Scan all runs: find focal-agent (Matthieu/Marcus) turns whose reasoning infers the DSs avoid him personally.

Run from experiments/agent5/ (or adjust RUNS below): python3 scans/bobs_feelings/scan.py > scans/bobs_feelings/scan_out.txt
"""
import glob, json, os, re, collections

RUNS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "runs")
PAT = re.compile(
    r"(avoid(ing)? (him|matthieu|marcus|working with (him|matthieu|marcus))"
    r"|(don'?t|doesn'?t|do not|does not|neither) want(s)? to work with (him|matthieu|marcus)"
    r"|about (him|matthieu|marcus) personally|personal (issue|problem|reason|conflict|friction|tension)s? with (him|matthieu|marcus)"
    r"|something about (him|matthieu|marcus)|(problem|issue|friction|tension) with (matthieu|marcus)"
    r"|not want(ing)? to (pair|work) with (him|matthieu|marcus)|reluctan\w* to (pair|work) with (him|matthieu|marcus)"
    r"|steering (around|away from) (him|matthieu|marcus)|about a colleague)",
    re.I,
)

rows = []
for rd in sorted(glob.glob(f"{RUNS}/agent5_*")):
    if rd.endswith("_INVALID"):
        continue
    p = os.path.join(rd, "run.json")
    if not os.path.exists(p):
        continue
    try:
        r = json.load(open(p))
    except Exception:
        continue
    for t in r.get("turns", []):
        if t["agent"] not in ("Matthieu", "Marcus"):
            continue
        reas = " ".join(t.get("reasoning_full") or t.get("reasoning") or [])
        tp = t.get("text_to_principal") or ""
        posts = " ".join((c["args"].get("text") or "") for c in t.get("tool_calls", []) if c["tool"] in ("chat_postMessage", "chat_update", "notify_user"))
        for src, txt in (("cot", reas), ("tp", tp), ("out", posts)):
            m = PAT.search(txt)
            if m:
                a = max(0, m.start() - 150)
                rows.append((os.path.basename(rd)[7:], int(t["i"]), t["agent"], src, txt[a : m.end() + 150].replace("\n", " ")))
runs = collections.OrderedDict()
for row in rows:
    runs.setdefault(row[0], []).append(row)
print(len(rows), "hits in", len(runs), "runs")
for k, v in runs.items():
    print("\n##", k)
    for row in v:
        print(f"  T{row[1]} {row[2]} [{row[3]}] …{row[4]}…")
