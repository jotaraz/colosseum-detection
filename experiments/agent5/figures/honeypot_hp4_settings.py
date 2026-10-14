"""hp4 (J1 message judge) verdicts per setting = cell x model, for 1.e.iv and 1.e.vi.

Settings with fewer than MIN_OP operative runs are left out, as is 1.e.vi deepseek.
One row per setting, four panels, one per gate: used_as_reason (all operative runs),
used_as_decisive_reason (explicit uses only - implicit uses are always `unclear` by schema),
reason_withdrawn (explicit + implicit uses) and presented_as_fixed (all operative runs).
Bars are 100%-stacked with counts printed in the segments.

Run set: the operative runs (T1's first meeting put at 16:00-17:00, honeypot_operative.operative,
recomputed here) among the valid ones - honeypot_set_1eiv.txt for 1.e.iv (180 of 209) and
honeypot_set_1evi.txt + honeypot_set_1evi_opus5or.txt for 1.e.vi (30 of 30).

Writes findings/honeypot_hp4_settings.png and .pdf.
Run from anywhere: python experiments/agent5/figures/honeypot_hp4_settings.py
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

A5 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(A5))
from honeypot_operative import operative  # noqa: E402

JUDGE = "openai-gpt-5.6-terra"
MIN_OP = 10
DROP = {("1.e.vi", "deepseek")}
CELLS = [("1.e.iv", A5 / "outputs/honeypot_hp4msg_1eiv", ["honeypot_set_1eiv.txt"]),
         ("1.e.vi", A5 / "outputs/honeypot_hp4msg_1evi",
          ["honeypot_set_1evi.txt", "honeypot_set_1evi_opus5or.txt"])]

INK, MUTED, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
# one blue ramp, strongest -> weakest; grey = the gate did not fire; hatched = unclear / n/a
B = ["#0d3f86", "#2a78d6", "#8cb9ec"]
NO, UNC = "#d6d5d0", "#f1f0ec"
PANELS = [
    ("used_as_reason", "Used as a reason\n(all operative runs)", None,
     [("explicit", B[0]), ("implicit", B[1]), ("no", NO), ("unclear", UNC)]),
    ("used_as_decisive_reason", "Decisive?\n(explicit uses)", {"explicit"},
     [("sole", B[0]), ("leading", B[1]), ("supporting", B[2]), ("unclear", UNC), ("n/a", UNC)]),
    ("reason_withdrawn", "Withdrawn by the end?\n(explicit + implicit uses)", {"explicit", "implicit"},
     [("stood", B[0]), ("withdrawn", B[2]), ("unclear", UNC), ("n/a", UNC)]),
    ("presented_as_fixed", "Hour presented as fixed\n(all operative runs)", None,
     [("explicit", B[0]), ("implicit", B[1]), ("movable", B[2]), ("not_raised", NO), ("unclear", UNC)]),
]


def load():
    rows = {}
    for cell, vdir, sets in CELLS:
        for s in sets:
            for name in (A5 / s).read_text().split():
                v = json.loads((vdir / f"{name}.{JUDGE}.json").read_text())
                calls = [json.loads(l) for l in (A5 / "runs" / name / "world_calls.jsonl").read_text().splitlines()
                         if l.strip()]
                model = re.search(r"_conc_(.+?)_s\d+_", name).group(1)
                if not operative(calls)[0]:
                    continue
                rows.setdefault((cell, model), []).append(v)
    return rows


def main():
    rows = load()
    use = lambda vs: sum(v["used_as_reason"] in ("explicit", "implicit") for v in vs) / len(vs)
    # keep settings with >= MIN_OP operative runs; 1.e.vi deepseek dropped as it reads like its 1.e.iv twin.
    # Both cells in one list, sorted by the share of runs that use the hour (explicit + implicit).
    order = sorted((k for k in rows if len(rows[k]) >= MIN_OP and k not in DROP), key=lambda k: -use(rows[k]))
    ys = {k: i for i, k in enumerate(order)}
    y = len(order)

    fig, axes = plt.subplots(1, 4, figsize=(15, 0.36 * y + 2.2), sharey=True, facecolor=SURF)
    for ax, (gate, title, cond, cats) in zip(axes, PANELS):
        ax.set_facecolor(SURF)
        for k in order:
            vs = [v for v in rows[k] if cond is None or v["used_as_reason"] in cond]
            c = Counter(v[gate] for v in vs)
            unknown = set(c) - {n for n, _ in cats}
            assert not unknown, (k, gate, unknown)
            if not vs:
                ax.text(0.01, ys[k], "none", va="center", fontsize=7.5, color=MUTED, style="italic")
                continue
            left = 0
            for n, col in cats:
                w = c[n] / len(vs)
                if not w:
                    continue
                hatch = "////" if col == UNC else None
                ax.barh(ys[k], w - 0.006, left=left + 0.003, height=0.72, color=col, hatch=hatch,
                        edgecolor="#b5b4ae" if hatch else col, linewidth=0)
                if w * 100 >= 7:
                    ax.text(left + w / 2, ys[k], str(c[n]), ha="center", va="center", fontsize=7.5,
                            color="white" if col in (B[0], B[1]) else INK)
                left += w
            ax.text(1.02, ys[k], f"{len(vs)}", va="center", fontsize=7, color=MUTED)
        ax.set_xlim(0, 1)
        ax.set_xticks([0, .5, 1], ["0", "50%", "100%"], fontsize=7.5, color=MUTED)
        ax.set_title(title, fontsize=9.5, color=INK, loc="left")
        for s in ("top", "right", "left"):
            ax.spines[s].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
        ax.tick_params(axis="y", length=0)
        ax.grid(axis="x", color=GRID, linewidth=0.6)
        ax.set_axisbelow(True)
        seen = {}
        for n, col in cats:
            if n not in seen and n != "n/a":
                seen[n] = Patch(facecolor=col, hatch="////" if col == UNC else None,
                                edgecolor="#b5b4ae" if col == UNC else col, label=n.replace("_", " "))
        ax.legend(handles=list(seen.values()), loc="upper center", bbox_to_anchor=(0.5, -0.045 * 30 / y),
                  ncol=3, fontsize=7.5, frameon=False, handlelength=1.2, columnspacing=0.8)

    labels = []
    for k in order:
        vs = rows[k]
        labels.append(f"{k[1]}{'*' if k[0] == '1.e.vi' else ''}   n={len(vs)}")
    axes[0].set_yticks([ys[k] for k in order], labels, fontsize=8, color=INK)
    axes[0].invert_yaxis()
    fig.text(0.01, 0.02, "* 1.e.vi (squeeze, no T2 push in Priya's ask); all other rows 1.e.iv (squeeze + T2 push)",
             fontsize=8, color=MUTED, ha="left")
    fig.suptitle("hp4 J1 verdicts per setting (cell × model), gpt-5.6-terra judge — operative runs, settings with n ≥ 10; "
                 "right of each bar: runs in that panel's base", x=0.01, ha="left", fontsize=10.5, color=INK)
    fig.subplots_adjust(left=0.15, right=0.985, top=1 - 0.9 / (0.36 * y + 2.2), bottom=1.1 / (0.36 * y + 2.2),
                        wspace=0.12)
    out = A5 / "findings/honeypot_hp4_settings"
    fig.savefig(f"{out}.png", dpi=200, facecolor=SURF)
    fig.savefig(f"{out}.pdf", facecolor=SURF)
    print(f"wrote {out}.png and .pdf")
    for k in order:
        vs = rows[k]
        print(k, len(vs), Counter(v["used_as_reason"] for v in vs))


if __name__ == "__main__":
    main()
