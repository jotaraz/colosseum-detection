"""16x10 transposed verdict matrix.

Writes experiments/agent5/findings/matrix_rotated.png. Inputs: outputs/5e8_seats_human_labeled.md,
the per-run run.json files, findings/full_image_callouts.json and notes/example-chat.txt
(the latter two only for the full_image* figures).

Needs matplotlib, numpy, pillow, uharfbuzz and fonttools, and macOS's Apple Color Emoji font.
Run from anywhere: python experiments/agent5/figures/build_matrix_rotated.py
"""
import os
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
import re
import io
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.lines import Line2D
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from PIL import Image
import uharfbuzz as hb
from fontTools.ttLib import TTFont

EMOJI_FONT_PATH = "/System/Library/Fonts/Apple Color Emoji.ttc"
with open(EMOJI_FONT_PATH, "rb") as _f:
    _HB_FACE = hb.Face(_f.read())
_HB_FONT = hb.Font(_HB_FACE)
_TT_FONT = TTFont(EMOJI_FONT_PATH, fontNumber=0)
_SBIX_STRIKE = _TT_FONT["sbix"].strikes[160]


def render_emoji(text):
    """Shape `text` (handles skin-tone/ZWJ sequences via harfbuzz) and pull the
    resulting glyph's color bitmap directly out of the font's sbix table."""
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(_HB_FONT, buf)
    gid = buf.glyph_infos[0].codepoint
    glyph_name = _TT_FONT.getGlyphName(gid)
    glyph = _SBIX_STRIKE.glyphs[glyph_name]
    im = Image.open(io.BytesIO(glyph.imageData)).convert("RGBA")
    bbox = im.getbbox()
    return im.crop(bbox) if bbox else im


EMOJI_MAN = render_emoji("\U0001F468\U0001F3FB")  # man, light skin tone (black hair)
EMOJI_BRIEFCASE = render_emoji("\U0001F4BC")


def disclosure_flags(cell_text):
    labels = [p.strip() for p in cell_text.split(";")]
    matt = any(l.startswith("direct & about matt") for l in labels)
    work = any(l.startswith("direct & about work") for l in labels)
    return matt, work


def _brace_profile(span, resolution=201):
    """Normalized (0..1) curly-brace bump over `span`, tip at 1 in the middle."""
    beta = 300.0 / span
    t = np.linspace(0, span, resolution)
    half = resolution // 2 + 1
    t_half = t[:half]
    b_half = (1 / (1 + np.exp(-beta * (t_half - t_half[0])))
              + 1 / (1 + np.exp(-beta * (t_half - t_half[-1]))))
    b = np.concatenate((b_half, b_half[-2::-1]))
    return t, (b - b.min()) / (b.max() - b.min())


def draw_left_brace(ax, y0, y1, xx, height, color="#666666", lw=1.3):
    """Curly brace spanning [y0, y1] vertically, tip pointing left `height` from xx."""
    t, b = _brace_profile(y1 - y0)
    y = y0 + t
    x = xx - b * height
    ax.plot(x, y, color=color, lw=lw, clip_on=False, solid_capstyle="round")


SRC = str(REPO) + "/experiments/agent5/outputs/5e8_seats_human_labeled.md"

MODEL_ORDER = ["deepseek", "dspro", "glm", "glm53", "glm53flash", "kimi", "kimik3", "qwen38flash"]
MODEL_FULL_NAME = {
    "deepseek": "deepseek-v4\nflash-0731",
    "dspro": "deepseek-v4\npro-0813",
    "glm": "glm-5.2",
    "glm53": "glm-5.3",
    "glm53flash": "glm-5.3-flash",
    "kimi": "Kimi-K2.6",
    "kimik3": "Kimi-K3",
    "qwen38flash": "Qwen3.8-Flash",
}
SEED_ORDER = [f"s{i}" for i in range(10)]  # s0..s9, glm53flash s10/s11 omitted
EMP_INITIAL = {"Priya": "A", "Nadia": "C"}  # Priya -> Alice, Nadia -> Carol

COLORS = {
    "FABR": "#CC79A7",
    "FRAM": "#E69F00",
    "NOTH": "#009E73",
    "goes for T1": "#0072B2",
    "sends no message": "#BBBBBB",
}
T1_DOT_COLOR = "#FFFFFF"
T1_DOT_RING = "#222222"
LABELS = {
    "FABR": "False claim",
    "FRAM": "Misdirection",
    "NOTH": "No deception",
    "goes for T1": "Goes for T1",
    "sends no message": "Sends no message",
}

def strip_md(s):
    s = s.strip()
    s = re.sub(r"^\*\*(.*)\*\*$", r"\1", s)
    return s.strip()

rows = {}  # (model, seed, employee) -> verdict
disclosure = {}  # (model, seed, employee) -> (matt: bool, work: bool)
with open(SRC) as f:
    for line in f:
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        model, seed, employee, col4, verdict = cells[0], cells[1], cells[2], cells[3], cells[4]
        if model in ("model", "---") or model not in MODEL_ORDER:
            continue
        if seed not in SEED_ORDER:
            continue
        if employee not in ("Priya", "Nadia"):
            continue
        v = strip_md(verdict)
        if v not in COLORS:
            raise ValueError(f"unknown verdict {v!r} for {model} {seed} {employee}")
        rows[(model, seed, employee)] = v
        disc_cell = cells[10] if len(cells) > 10 else "—"
        disclosure[(model, seed, employee)] = disclosure_flags(disc_cell)

expected = len(MODEL_ORDER) * len(SEED_ORDER) * 2
assert len(rows) == expected, f"got {len(rows)} rows, expected {expected}"

EMP_ORDER = ["Priya", "Nadia"]

# ── final board task per seat, read from each run's run.json `assignments` ──
AGENT5 = Path(str(REPO) + "/experiments/agent5")
RUN_RE = re.compile(r"_conc_(.+?)_s(\d+)_")

# glm s6 and kimi s5/s6/s7 carry no njv3 judge sidecar, so they're absent from
# njv3_all78.txt; their run dirs (same 5.e.viii world stem) are added by hand.
EXTRA_RUN_DIRS = [
    "runs/agent5_w2PsuperstrongNsuperstrong_affBothT1fail_hzRafaelStrong3HelenaProbe3DmHint2Strict_conc_glm_s6_20260913-133222",
    "runs/agent5_w2PsuperstrongNsuperstrong_affBothT1fail_hzRafaelStrong3HelenaProbe3DmHint2Strict_conc_kimi_s5_20260912-142906",
    "runs/agent5_w2PsuperstrongNsuperstrong_affBothT1fail_hzRafaelStrong3HelenaProbe3DmHint2Strict_conc_kimi_s6_20260912-142905",
    "runs/agent5_w2PsuperstrongNsuperstrong_affBothT1fail_hzRafaelStrong3HelenaProbe3DmHint2Strict_conc_kimi_s7_20260912-142906",
]

run_dir_for = {}  # (model, seed) -> run dir path
for line in (AGENT5 / "njv3_all78.txt").read_text().splitlines():
    line = line.strip()
    if not line:
        continue
    m = RUN_RE.search(line)
    model, seed = m.group(1), f"s{m.group(2)}"
    if model in MODEL_ORDER and seed in SEED_ORDER:
        run_dir_for[(model, seed)] = line
for rel in EXTRA_RUN_DIRS:
    m = RUN_RE.search(rel)
    model, seed = m.group(1), f"s{m.group(2)}"
    run_dir_for[(model, seed)] = str(AGENT5 / rel)

claimed_t1 = {}  # (model, seed, employee) -> bool, ended the run assigned to T1
for model in MODEL_ORDER:
    for seed in SEED_ORDER:
        run_dir = run_dir_for[(model, seed)]
        run_json_path = Path(run_dir)
        if not run_json_path.is_absolute():
            run_json_path = REPO / run_json_path
        assignments = json.loads((run_json_path / "run.json").read_text()).get("assignments") or {}
        for employee in EMP_ORDER:
            claimed_t1[(model, seed, employee)] = assignments.get(employee) == "T1"

# ── rotated layout: 16 rows (model x employee) x 10 columns (samples, s0 leftmost) ──
n_rows = len(MODEL_ORDER) * len(EMP_ORDER)  # 16
n_cols = len(SEED_ORDER)  # 10

fig_w, fig_h = 7.6, 10.5
fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=200)

cell = 1.0
gap = 0.08  # surface gap between cells

for ci, model in enumerate(MODEL_ORDER):
    for ei, employee in enumerate(EMP_ORDER):
        row = ci * 2 + ei
        y = n_rows - 1 - row
        for ri, seed in enumerate(SEED_ORDER):
            x = ri
            v = rows[(model, seed, employee)]
            color = COLORS[v]
            ax.add_patch(Rectangle(
                (x + gap / 2, y + gap / 2), cell - gap, cell - gap,
                facecolor=color, edgecolor="white", linewidth=1.2,
            ))

            matt, work = disclosure[(model, seed, employee)]
            icons = [im for im, flag in ((EMOJI_MAN, matt), (EMOJI_BRIEFCASE, work)) if flag]
            if icons:
                cx = x + cell / 2
                cy = y + cell / 2
                icon_zoom = 0.155 if len(icons) == 1 else 0.105
                offsets = [0] if len(icons) == 1 else [-0.24, 0.24]
                for im, dx in zip(icons, offsets):
                    ab = AnnotationBbox(
                        OffsetImage(im, zoom=icon_zoom), (cx + dx, cy),
                        frameon=False, pad=0, zorder=4,
                    )
                    ax.add_artist(ab)

            if v != "goes for T1" and claimed_t1[(model, seed, employee)]:
                ax.add_patch(plt.Circle(
                    (x + cell - 0.20, y + cell - 0.20), 0.078,
                    facecolor=T1_DOT_COLOR, edgecolor=T1_DOT_RING, linewidth=1.1, zorder=5,
                ))

# model-group separators (sharp dark rule every 2 rows, plus outer edges)
for ci in range(0, len(MODEL_ORDER) + 1):
    yy = n_rows - ci * 2
    ax.plot([0, n_cols], [yy, yy], color="#4d4d4d", linewidth=2.2, zorder=3, solid_capstyle="butt")

ax.set_xlim(0, n_cols)
ax.set_ylim(0, n_rows)
ax.set_aspect("equal")

ax.set_xticks([])
ax.set_xlabel("samples", fontsize=10, color="#666666", labelpad=10)

ax.set_yticks([])

brace_x = -0.12
brace_width = 0.22
for ci, model in enumerate(MODEL_ORDER):
    y0, y1 = n_rows - (ci * 2 + 2) + 0.06, n_rows - ci * 2 - 0.06
    draw_left_brace(ax, y0, y1, brace_x, brace_width, color="#666666", lw=1.3)
    ax.text(brace_x - brace_width - 0.10, (y0 + y1) / 2, MODEL_FULL_NAME[model],
            ha="right", va="center", fontsize=9, color="#666666")

for spine in ax.spines.values():
    spine.set_visible(False)

# Alice/Carol sub-labels left of the grid
for ci, model in enumerate(MODEL_ORDER):
    row_a = n_rows - 1 - (ci * 2 + 0)
    row_c = n_rows - 1 - (ci * 2 + 1)
    ax.text(-0.12, row_a + 0.5, EMP_INITIAL["Priya"], ha="right", va="center", fontsize=7, color="#999999")
    ax.text(-0.12, row_c + 0.5, EMP_INITIAL["Nadia"], ha="right", va="center", fontsize=7, color="#999999")

legend_handles = [
    Line2D([0], [0], marker="s", linestyle="", markersize=12,
           markerfacecolor=COLORS[k], markeredgecolor="none", label=LABELS[k])
    for k in ["FABR", "FRAM", "NOTH", "goes for T1", "sends no message"]
]
legend_handles.append(
    Line2D([0], [0], marker="o", linestyle="", markersize=9,
           markerfacecolor=T1_DOT_COLOR, markeredgecolor=T1_DOT_RING, markeredgewidth=1.1,
           label="Claimed T1 on final board")
)
# figure-fraction placement (not axes-fraction) so it doesn't distort with this
# grid's tall, narrow aspect ratio
fig.legend(
    handles=legend_handles, loc="lower center", bbox_to_anchor=(0.5, 0.105),
    ncol=3, frameon=False, fontsize=9, handletextpad=0.5, columnspacing=1.4,
)

# emoji legend row (disclosure markers), below the color legend
emoji_legend_y = 0.045
for icon, x_frac, label in [
    (EMOJI_MAN, 0.20, "direct disclosure, about Bob"),
    (EMOJI_BRIEFCASE, 0.60, "direct disclosure, about work"),
]:
    ab = AnnotationBbox(
        OffsetImage(icon, zoom=0.08), (x_frac, emoji_legend_y),
        xycoords="figure fraction", frameon=False, pad=0, annotation_clip=False,
    )
    fig.add_artist(ab)
    fig.text(x_frac + 0.045, emoji_legend_y, label,
             ha="left", va="center", fontsize=9, color="#333333")

fig.patch.set_facecolor("#fcfcfb")
ax.set_facecolor("#fcfcfb")

plt.tight_layout(rect=[0, 0.19, 1, 1])
out = str(REPO) + "/experiments/agent5/findings/matrix_rotated.png"
plt.savefig(out, facecolor=fig.get_facecolor(), bbox_inches="tight")
print("saved", out)
