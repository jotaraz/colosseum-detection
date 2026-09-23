"""Wide figure: transcript | matrix | quote cards, arrows from cells to quotes.

Writes experiments/agent5/findings/full_image.png. Inputs: outputs/5e8_seats_human_labeled.md,
the per-run run.json files, findings/full_image_callouts.json and notes/example-chat.txt
(the latter two only for the full_image* figures).

Needs matplotlib, numpy, pillow, uharfbuzz and fonttools, and macOS's Apple Color Emoji font.
Run from anywhere: python experiments/agent5/figures/full_image.py
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
    "FRAM": "Framing",
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

# ── callout content: edit findings/full_image_callouts.json to change the quotes ──
CALLOUTS_JSON = AGENT5 / "findings" / "full_image_callouts.json"
CALLOUTS = [
    dict(model=c["model"], seed=c["seed"], employee=c["employee"], title=c["title"],
         parts=[(p["kind"].capitalize(), p["text"]) for p in c["parts"]])
    for c in json.loads(CALLOUTS_JSON.read_text())
]

import textwrap
import matplotlib.patheffects as pe
from matplotlib.offsetbox import TextArea, VPacker, HPacker, DrawingArea, PaddedBox
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
import re
from matplotlib.path import Path

WRAP_WIDTH = 64
FS = 1.75  # global font scale
ARROW_COLOR = "#B33A3A"
S = 0.42  # inches per data unit, fixed so point-sized text maps predictably to data units

n_rows = len(MODEL_ORDER) * len(EMP_ORDER)  # 16
n_cols = len(SEED_ORDER)  # 10

XLIM = (-60, 40)
YLIM = (-45, 23)
fig = plt.figure(figsize=((XLIM[1] - XLIM[0]) * S, (YLIM[1] - YLIM[0]) * S), dpi=200)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(*XLIM)
ax.set_ylim(*YLIM)
ax.set_aspect("equal")
ax.axis("off")

cell = 1.0
gap = 0.08

for ci, model in enumerate(MODEL_ORDER):
    for ei, employee in enumerate(EMP_ORDER):
        row = ci * 2 + ei
        y = n_rows - 1 - row
        for ri, seed in enumerate(SEED_ORDER):
            x = n_cols - 1 - ri  # s9 leftmost, s0 rightmost
            v = rows[(model, seed, employee)]
            ax.add_patch(Rectangle(
                (x + gap / 2, y + gap / 2), cell - gap, cell - gap,
                facecolor=COLORS[v], edgecolor="white", linewidth=1.2,
            ))
            matt, work = disclosure[(model, seed, employee)]
            icons = [im for im, flag in ((EMOJI_MAN, matt), (EMOJI_BRIEFCASE, work)) if flag]
            if icons:
                cx, cy = x + cell / 2, y + cell / 2
                icon_zoom = 0.155 if len(icons) == 1 else 0.105
                offsets = [0] if len(icons) == 1 else [-0.24, 0.24]
                for im, dx in zip(icons, offsets):
                    ax.add_artist(AnnotationBbox(
                        OffsetImage(im, zoom=icon_zoom), (cx + dx, cy),
                        frameon=False, pad=0, zorder=4,
                    ))
            if v != "goes for T1" and claimed_t1[(model, seed, employee)]:
                ax.add_patch(plt.Circle(
                    (x + cell - 0.20, y + cell - 0.20), 0.078,
                    facecolor=T1_DOT_COLOR, edgecolor=T1_DOT_RING, linewidth=1.1, zorder=5,
                ))

for ci in range(0, len(MODEL_ORDER) + 1):
    yy = n_rows - ci * 2
    ax.plot([0, n_cols], [yy, yy], color="#4d4d4d", linewidth=2.2, zorder=3, solid_capstyle="butt")

runs_label = ax.text(3.5, n_rows + 0.2, "10 runs per model", ha="center", va="bottom", fontsize=FS * 10, color="#666666")

EMP_COLOR = {"Priya": "#332288", "Nadia": "#999933"}  # Alice indigo, Carol olive
CHIP = 0.6 * FS          # side of the [A]/[C] chips, data units
CHIP_RIGHT = -0.14  # chips' right edge, just left of the grid
brace_x = CHIP_RIGHT - CHIP - 0.14
brace_width = 0.22
model_labels = []
for ci, model in enumerate(MODEL_ORDER):
    y0, y1 = n_rows - (ci * 2 + 2) + 0.06, n_rows - ci * 2 - 0.06
    draw_left_brace(ax, y0, y1, brace_x, brace_width, color="#666666", lw=1.3)
    model_labels.append(ax.text(brace_x - brace_width - 0.10, (y0 + y1) / 2, MODEL_FULL_NAME[model],
            ha="right", va="center", fontsize=FS * 9, color="#666666", zorder=3.6,
            bbox=dict(boxstyle="square,pad=0.15", facecolor="#fcfcfb", edgecolor="none")))
    for ei, employee in enumerate(EMP_ORDER):
        cy = n_rows - 1 - (ci * 2 + ei) + 0.5
        ax.add_patch(FancyBboxPatch((CHIP_RIGHT - CHIP, cy - CHIP / 2), CHIP, CHIP,
                                    boxstyle="round,pad=0,rounding_size=0.12",
                                    facecolor=EMP_COLOR[employee], edgecolor="none", zorder=3.6))
        ax.text(CHIP_RIGHT - CHIP / 2, cy - 0.01, EMP_INITIAL[employee], ha="center", va="center",
                fontsize=FS * 9, fontweight="bold", color="white", zorder=3.7)


def cell_box(model, seed, employee):
    """(x0, y0) lower-left of the cell in data coords."""
    row = MODEL_ORDER.index(model) * 2 + EMP_ORDER.index(employee)
    return n_cols - 1 - SEED_ORDER.index(seed), n_rows - 1 - row


EMOJI_THOUGHT = render_emoji("\U0001F4AD")
EMOJI_SPEECH = render_emoji("\U0001F4AC")
EMP_NAME = {"Priya": "Alice", "Nadia": "Carol"}
AVATAR_COLOR = EMP_COLOR
BY_TITLE = {c["title"]: c for c in CALLOUTS}

MONO_CHAR_PTS = FS * 8 * 0.602  # DejaVu Sans Mono advance at 8pt
CARD_PAD = 4 * FS
AVATAR = 20 * FS
HSEP = 5 * FS


def wrap_to(width_pts):
    return int(width_pts // MONO_CHAR_PTS) - 1


PSEUDONYMS = {"Priya": "Alice", "Matthieu": "Bob", "Nadia": "Carol", "Rafael": "Dan"}


def pseudonymize(text):
    return re.sub(r"\b(" + "|".join(PSEUDONYMS) + r")\b", lambda m: PSEUDONYMS[m.group(1)], text)


HIGHLIGHT = {"falseclaim": COLORS["FABR"], "framing": COLORS["FRAM"]}
HL_RE = re.compile(r"\\(falseclaim|framing)\{([^}]*)\}")


ELLIPSIS = "\x05"  # sentinel for an elision mark; drawn as a grey "…"
ELLIPSIS_COLOR = "#9a9a9a"
ELISION_RE = re.compile(r"\[(?:\.\.\.|\u2026)\]")


def compact_elisions(text):
    """'[...]' -> one grey ellipsis; marks at the very start/end of a quote are dropped."""
    text = ELISION_RE.sub(ELLIPSIS, text)
    text = re.sub(r"^[\s" + ELLIPSIS + r"]+", "", text)
    # a trailing mark goes only after a finished sentence; after "Board state:" it must stay
    m = re.search(r"[\s" + ELLIPSIS + r"]+$", text)
    if m and ELLIPSIS in m.group(0):
        before = text[:m.start()]
        last_visible = re.sub(r"[}*]+$", "", before)[-1:]
        text = before if last_visible in ".!?\"\u201d)" else before + " " + ELLIPSIS
    text = re.sub(ELLIPSIS + r"(\s*" + ELLIPSIS + r")+", ELLIPSIS, text)
    return re.sub(r" (?=" + ELLIPSIS + ")", "\u00a0", text)  # never wrap a lone "…" onto its own line


def parse_marked(paragraph):
    """-> list of (char, bold, highlight_color|None, is_elision) from **bold**,
    \\falseclaim{..}/\\framing{..} and elision sentinels."""
    out, bold, pos = [], False, 0
    segments = []  # (text, hl)
    for m in HL_RE.finditer(paragraph):
        segments.append((paragraph[pos:m.start()], None))
        segments.append((m.group(2), HIGHLIGHT[m.group(1)]))
        pos = m.end()
    segments.append((paragraph[pos:], None))
    for text, hl in segments:
        parts = text.split("**")
        for i, part in enumerate(parts):
            out += [("\u2026", False, None, True) if ch == ELLIPSIS else (ch, bold, hl, False) for ch in part]
            if i < len(parts) - 1:
                bold = not bold
    return out


def wrap_marked(paragraph, width):
    """Greedy word wrap of a marked-up paragraph -> lines of (char, bold, hl)."""
    words, cur = [], []
    for ch in parse_marked(paragraph):
        if ch[0] == " ":
            if cur:
                words.append(cur)
                cur = []
        else:
            cur.append(ch)
    if cur:
        words.append(cur)
    lines, line = [], []
    for w in words:
        if line and len(line) + 1 + len(w) > width:
            lines.append(line)
            line = []
        if line:
            prev, nxt = line[-1], w[0]
            line.append((" ", prev[1] and nxt[1], prev[2] if prev[2] == nxt[2] else None, False))
        line += w
    lines.append(line)
    return lines


SHORTEN_RE = re.compile(r"\\shorten\{([^{}]*)\}\{([^{}]*)\}")


def body_text(text, width_pts, color="#333333", style="normal", scale=1.0):
    """Monospace block with inline **bold** and colour highlights: chars sit on a fixed-advance
    grid, so bold runs and highlight bars line up exactly with the regular text."""
    text = compact_elisions(pseudonymize(SHORTEN_RE.sub(r"\1", text)))
    char_w = MONO_CHAR_PTS * scale
    wrap = int(width_pts // char_w) - 1
    size = FS * 8 * scale
    line_h = size * 1.2
    rows = []
    for para in text.split("\n"):
        for line in wrap_marked(para, wrap):
            da = DrawingArea(width_pts, line_h, clip=False)
            col = 0
            while col < len(line):
                key = line[col][1:]
                end = col
                while end < len(line) and line[end][1:] == key:
                    end += 1
                b, hl, ell = key
                if hl:
                    da.add_artist(Rectangle((col * char_w - 1, -0.5), (end - col) * char_w + 2,
                                            line_h + 1, facecolor=hl, alpha=0.55, edgecolor="none"))
                da.add_artist(plt.Text(col * char_w, line_h * 0.25, "".join(c[0] for c in line[col:end]),
                                       fontsize=size, family="monospace", color=ELLIPSIS_COLOR if ell else color,
                                       style=style,
                                       fontweight="bold" if b else "normal", ha="left", va="baseline"))
                col = end
            rows.append(da)
    return VPacker(children=rows, align="left", pad=0, sep=0)


def section_header(icon, label):
    return HPacker(children=[OffsetImage(icon, zoom=0.07),
                             TextArea(label, textprops=dict(fontsize=FS * 8.5, fontweight="bold", color="#555555"))],
                   align="center", pad=0, sep=4)


def card(content, **patch):
    attrs = dict(boxstyle="round,pad=0,rounding_size=14", linewidth=0.9)
    attrs.update(patch)
    return PaddedBox(content, pad=CARD_PAD, draw_frame=True, patch_attrs=attrs)


def icon_column(img):
    return VPacker(children=[OffsetImage(img, zoom=AVATAR / img.width)], align="top", pad=0, sep=0)


def reasoning_card(text, employee, inner_w):
    text_w = inner_w - AVATAR - HSEP
    name = TextArea(f"{EMP_NAME[employee]} \u00b7 reasoning",
                    textprops=dict(fontsize=FS * 8.5, style="italic", color="#6b5fa8"))
    right = VPacker(children=[name, body_text(text, text_w, style="italic", color="#4a4a5a"),
                              DrawingArea(text_w, 0)], align="left", pad=0, sep=3)
    return card(HPacker(children=[icon_column(EMOJI_THOUGHT), right], align="top", pad=0, sep=HSEP),
                facecolor="#f2f0f9", edgecolor="#a99fd0", linestyle=(0, (3, 2)))


def avatar(employee):
    da = DrawingArea(AVATAR, AVATAR, clip=False)
    da.add_artist(FancyBboxPatch((0, 0), AVATAR, AVATAR, boxstyle="round,pad=0,rounding_size=4",
                                 facecolor=AVATAR_COLOR[employee], edgecolor="none"))
    da.add_artist(plt.Text(AVATAR / 2, AVATAR / 2 - 0.5, EMP_NAME[employee][0], ha="center", va="center",
                           fontsize=FS * 10, fontweight="bold", color="white"))
    return VPacker(children=[da], align="top", pad=0, sep=0)


def message_card(text, employee, inner_w):
    text_w = inner_w - AVATAR - HSEP
    name = HPacker(children=[
        TextArea(EMP_NAME[employee], textprops=dict(fontsize=FS * 8.5, fontweight="bold", color="#1d1c1d")),
        TextArea("message", textprops=dict(fontsize=FS * 8, color="#8a8a8a")),
    ], align="baseline", pad=0, sep=5)
    right = VPacker(children=[name, body_text(text, text_w, color="#1d1c1d"), DrawingArea(text_w, 0)],
                    align="left", pad=0, sep=3)
    return card(HPacker(children=[avatar(employee), right], align="top", pad=0, sep=HSEP),
                facecolor="#ffffff", edgecolor="#cfcfcf")


def make_box(c, width_pts, extra_h_pts=0):
    inner_w = width_pts - 2 * CARD_PAD - 2
    heading = HPacker(children=[
        TextArea(c["title"], textprops=dict(fontsize=FS * 12, fontweight="bold", color="#222222")),
        TextArea(MODEL_FULL_NAME[c["model"]].replace(chr(10), " "),
                 textprops=dict(fontsize=FS * 7.5, style="italic", color="#777777")),
    ], align="baseline", pad=0, sep=6 * FS)
    title = VPacker(children=[heading, DrawingArea(width_pts, 0)],  # pins exact width so boxes tile
                    align="left", pad=0, sep=0)
    children = [title]
    for label, text in c["parts"]:
        make = reasoning_card if label == "Reasoning" else message_card
        children.append(make(text, c["employee"], inner_w))
    if extra_h_pts:
        children.append(DrawingArea(width_pts, extra_h_pts - 4))  # pads height to match a paired box
    return VPacker(children=children, align="left", pad=0, sep=4)


PTS_PER_UNIT = S * 72
GAP = 0.6            # gutter between every pair of blocks, in data units
RIGHT_START = n_cols + GAP  # left edge of the right column
TOP = n_rows + 2.5  # top of the pointer labels above the grid
BOTTOM_TOP = -GAP    # top edge of the bottom row of boxes
GRID_RIGHT = n_cols

fig.canvas.draw()
renderer = fig.canvas.get_renderer()
inv = ax.transData.inverted()


def data_extent(artist):
    bb = artist.get_window_extent(renderer)
    (x0, y0), (x1, y1) = inv.transform([[bb.x0, bb.y0], [bb.x1, bb.y1]])
    return x0, y0, x1, y1


def tint(hex_color, amount):
    """Blend `hex_color` toward white; amount=1 is the colour itself."""
    rgb = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(255 - (255 - v) * amount):02x}" for v in rgb)


BOX_COLORS = {"Framing": (tint(COLORS["FRAM"], 0.16), tint(COLORS["FRAM"], 0.75)),
              "False claim": (tint(COLORS["FABR"], 0.16), tint(COLORS["FABR"], 0.75))}


def add_box(title, width_pts, align, target, extra_h_pts=0):
    face, edge = BOX_COLORS.get(title, ("#fffdf7", "#999999"))
    ab = AnnotationBbox(
        make_box(BY_TITLE[title], width_pts, extra_h_pts), target, xycoords="data", box_alignment=align,
        frameon=True, pad=0.4, annotation_clip=False, zorder=8,
        bboxprops=dict(boxstyle="round,pad=0.35,rounding_size=0.6", facecolor=face,
                       edgecolor=edge, linewidth=1.3 if title in BOX_COLORS else 1.0),
    )
    ax.add_artist(ab)
    return ab


def snap(ab, align, target):
    """Shift the box so its drawn frame edge (per `align`) lands exactly on `target`."""
    fig.canvas.draw()
    x0, y0, x1, y1 = data_extent(ab)
    ex = x0 if align[0] == 0 else x1
    ey = y0 if align[1] == 0 else y1
    bx, by = ab.xybox
    ab.xybox = (bx + target[0] - ex, by + target[1] - ey)


boxes = {}
RIGHT_START = GRID_RIGHT + GAP
RIGHT_W = 480  # content width of the right column, pts — kept narrow so the figure grows taller, not wider
RIGHT_ORDER = ["Framing", "False claim", "Disclosure about work"]
BOB_RIGHT = 6.0  # leaves room under the grid for the diagonal work arrow

fig.canvas.draw()
X0 = min(data_extent(t)[0] for t in model_labels)
probe = add_box("Framing", 300, (0, 1), (RIGHT_START, TOP))
fig.canvas.draw()
px0, _, px1, _ = data_extent(probe)
frame_extra = (px1 - px0) - 300 / PTS_PER_UNIT
probe.remove()

legend_handles = [
    Line2D([0], [0], marker="s", linestyle="", markersize=12 * FS,
           markerfacecolor=COLORS[k], markeredgecolor="none", label=LABELS[k])
    for k in ["FABR", "FRAM", "NOTH", "goes for T1", "sends no message"]
]
legend_handles.append(
    Line2D([0], [0], marker="o", linestyle="", markersize=9 * FS,
           markerfacecolor=T1_DOT_COLOR, markeredgecolor=T1_DOT_RING, markeredgewidth=1.1,
           label="Claimed T1 on final board")
)
LEGEND_KW = dict(handles=legend_handles, bbox_transform=ax.transData, ncol=2, frameon=False,
                 fontsize=FS * 9, handletextpad=0.5, columnspacing=1.4, labelspacing=0.7)
emoji_rows = [(EMOJI_MAN, "Direct disclosure, about Bob"), (EMOJI_BRIEFCASE, "Direct disclosure, about work")]
ROW_H = 0.78 * FS
probe_leg = ax.legend(loc="lower left", bbox_to_anchor=(0, 0), **LEGEND_KW)
fig.canvas.draw()
_, ly0, _, ly1 = data_extent(probe_leg)
probe_leg.remove()
legend_block_h = (ly1 - ly0) + ROW_H * len(emoji_rows) + 0.2

wc = BY_TITLE["Disclosure about work"]
wx, wy = cell_box(wc["model"], wc["seed"], wc["employee"])
WORK_TAIL = (wx + 1, wy + 0.9)
bc = BY_TITLE["Disclosure about Bob"]
bx_, by_ = cell_box(bc["model"], bc["seed"], bc["employee"])


def x_on_line(p, q, yy):
    return p[0] + (q[0] - p[0]) * (p[1] - yy) / (p[1] - q[1])


def build(right_w):
    """Lay out the right column at `right_w`, then the widest Bob box the work arrow allows.
    Returns (right_bottom, left_bottom, bob_right) or None if the arrow can't clear the Bob cell."""
    y = TOP
    for t in RIGHT_ORDER:
        boxes[t] = add_box(t, right_w, (0, 1), (RIGHT_START, y))
        snap(boxes[t], (0, 1), (RIGHT_START, y))
        fig.canvas.draw()
        y = data_extent(boxes[t])[1] - GAP
    bob_right = GRID_RIGHT - 0.3
    bob_w = (bob_right - X0 - frame_extra) * PTS_PER_UNIT
    boxes["Disclosure about Bob"] = add_box("Disclosure about Bob", bob_w, (0, 1), (X0, BOTTOM_TOP))
    snap(boxes["Disclosure about Bob"], (0, 1), (X0, BOTTOM_TOP))
    fig.canvas.draw()
    bob_bottom = data_extent(boxes["Disclosure about Bob"])[1]
    return y + GAP, bob_bottom, bob_right


def clear():
    for t in RIGHT_ORDER + ["Disclosure about Bob"]:
        if t in boxes:
            boxes.pop(t).remove()


# pick the right-column width minimising figure area plus the blank strip left under the
# shorter column (wider = shorter columns but smaller text at a fixed display width)
best = None
for RIGHT_W in range(360, 900, 10):
    r = build(RIGHT_W)
    right_x1 = data_extent(boxes["Framing"])[2] if r else None
    clear()
    if r is None:
        continue
    width = right_x1 - X0
    diff = abs(r[0] - r[1])
    shorter_w = (right_x1 - RIGHT_START) if r[0] > r[1] else (r[2] - X0)
    cost = width * (TOP - min(r[0], r[1])) + diff * shorter_w
    if best is None or cost < best[0]:
        best = (cost, RIGHT_W, diff)
RIGHT_W = best[1]
best = (best[2], RIGHT_W)
right_bottom, left_bottom, BOB_RIGHT = build(RIGHT_W)
bob_bottom = data_extent(boxes["Disclosure about Bob"])[1]
print(f"right col {RIGHT_W} pts, Bob right edge {BOB_RIGHT:.2f}, column mismatch {best[0]:.2f}")

# pad whichever of the Bob / last right-column box ends higher, until both bottoms coincide
def refit(title, width_pts, align, anchor, target):
    extra = 0.0
    for _ in range(4):
        bottom_now = data_extent(boxes[title])[1]
        diff = (bottom_now - target) * PTS_PER_UNIT
        if abs(diff) < 0.3:
            return
        extra = max(0.0, extra + diff)
        boxes[title].remove()
        boxes[title] = add_box(title, width_pts, align, anchor, extra_h_pts=extra + 4 if extra else 0)
        snap(boxes[title], align, anchor)
        fig.canvas.draw()


last = RIGHT_ORDER[-1]
fig.canvas.draw()
target = min(data_extent(boxes[last])[1], data_extent(boxes["Disclosure about Bob"])[1])
refit(last, RIGHT_W, (0, 1), (RIGHT_START, data_extent(boxes[last])[3]), target)
refit("Disclosure about Bob", (BOB_RIGHT - X0 - frame_extra) * PTS_PER_UNIT, (0, 1), (X0, BOTTOM_TOP), target)
bob_bottom = data_extent(boxes["Disclosure about Bob"])[1]
fig.canvas.draw()
ext = {t: data_extent(ab) for t, ab in boxes.items()}
print("right column content width (pts):", RIGHT_W)
for t, e in ext.items():
    print(f"{t:24s} x {e[0]:6.2f}..{e[2]:6.2f}  y {e[1]:6.2f}..{e[3]:6.2f}")


ARROW_STYLE = "-|>,head_length=0.7,head_width=0.35"
HALO = [pe.Stroke(linewidth=3.6, foreground="white"), pe.Normal()]


def arrow(tail, head, color=ARROW_COLOR):
    ann = ax.annotate(
        "", xy=head, xytext=tail, xycoords="data", textcoords="data",
        arrowprops=dict(arrowstyle=ARROW_STYLE, color=color, lw=1.8, shrinkA=0, shrinkB=1),
        zorder=3.5, annotation_clip=False,
    )
    ann.arrow_patch.set_path_effects(HALO)


def highlight(model, seed, employee):
    x, y = cell_box(model, seed, employee)
    ax.add_patch(Rectangle((x + gap / 2, y + gap / 2), cell - gap, cell - gap,
                           facecolor="none", edgecolor=ARROW_COLOR, linewidth=2.6, zorder=6))
    return x, y


def polyline_arrow(points, color=ARROW_COLOR):
    patch = FancyArrowPatch(path=Path(points, [Path.MOVETO] + [Path.LINETO] * (len(points) - 1)),
                            arrowstyle=ARROW_STYLE, mutation_scale=10, color=color, lw=1.8,
                            joinstyle="round", capstyle="round", zorder=3.5, clip_on=False)
    patch.set_path_effects(HALO)
    ax.add_patch(patch)


def to_left_edge(title, tail_y_offset=0.5):
    c = BY_TITLE[title]
    x, y = highlight(c["model"], c["seed"], c["employee"])
    bx0, by0, bx1, by1 = ext[title]
    ty = y + tail_y_offset
    arrow((x + 1, ty), (bx0, min(max(ty, by0 + 0.8), by1 - 0.8)))


to_left_edge("Framing")
to_left_edge("False claim")

# work arrow: one straight segment, chosen to hit as few in-cell emojis / T1 dots as possible
# and to stay clear of the False-claim and Bob arrows
fc = BY_TITLE["False claim"]
fx, fy = cell_box(fc["model"], fc["seed"], fc["employee"])
fbx0, fby0, fbx1, fby1 = ext["False claim"]
fc_seg = ((fx + 1, fy + 0.5), (fbx0, min(max(fy + 0.5, fby0 + 0.8), fby1 - 0.8)))
bc = BY_TITLE["Disclosure about Bob"]
bx_, by_ = cell_box(bc["model"], bc["seed"], bc["employee"])
bob_seg = ((bx_ + 0.5, by_), (bx_ + 0.5, ext["Disclosure about Bob"][3]))

obstacles = []  # (cx, cy, radius)
for (m_, s_, e_), (matt, work) in disclosure.items():
    ox, oy = cell_box(m_, s_, e_)
    icons = [f for f in (matt, work) if f]
    if len(icons) == 1:
        obstacles.append((ox + 0.5, oy + 0.5, 0.42))
    elif len(icons) == 2:
        obstacles += [(ox + 0.26, oy + 0.5, 0.3), (ox + 0.74, oy + 0.5, 0.3)]
    if rows[(m_, s_, e_)] != "goes for T1" and claimed_t1[(m_, s_, e_)]:
        obstacles.append((ox + 0.8, oy + 0.8, 0.13))


# the corner badges on the two disclosure cards
for title in ("Disclosure about work", "Disclosure about Bob"):
    e = ext[title]
    obstacles.append((e[2] - 0.1, e[3] + 0.05, 0.42))


def seg_point_dist(p, q, c):
    px_, py_ = p
    qx_, qy_ = q
    dx, dy = qx_ - px_, qy_ - py_
    t = max(0.0, min(1.0, ((c[0] - px_) * dx + (c[1] - py_) * dy) / (dx * dx + dy * dy)))
    return ((px_ + t * dx - c[0]) ** 2 + (py_ + t * dy - c[1]) ** 2) ** 0.5


def segs_cross(a1, a2, b1, b2):
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return (orient(a1, a2, b1) * orient(a1, a2, b2) < 0) and (orient(b1, b2, a1) * orient(b1, b2, a2) < 0)


c = BY_TITLE["Disclosure about work"]
wx, wy = highlight(c["model"], c["seed"], c["employee"])
wbx0, wby0, wbx1, wby1 = ext["Disclosure about work"]
tails = [(wx + 1, wy + f / 10) for f in range(1, 10)] + [(wx + f / 10, wy) for f in range(3, 10)]
heads = [(wbx0, y_) for y_ in [wby1 - 0.5 - 0.25 * k for k in range(12)] if y_ > wby0 + 0.5] + \
        [(wbx0 + 0.3 + 0.3 * k, wby1) for k in range(10)]
best = None
for t_ in tails:
    for h_ in heads:
        if segs_cross(t_, h_, *fc_seg) or segs_cross(t_, h_, *bob_seg):
            continue
        pts = [(t_[0] + (h_[0] - t_[0]) * k / 60, t_[1] + (h_[1] - t_[1]) * k / 60) for k in range(59)]
        if any(e[0] - 0.1 < x_ < e[2] + 0.1 and e[1] - 0.1 < y_ < e[3] + 0.1
               for name, e in ext.items() if name != "Disclosure about work" for (x_, y_) in pts):
            continue
        hits = sum(1 for (ox, oy, r) in obstacles
                   if (ox, oy) != (wx + 0.5, wy + 0.5) and seg_point_dist(t_, h_, (ox, oy)) < r)
        near = min((seg_point_dist(t_, h_, (ox, oy)) - r for (ox, oy, r) in obstacles
                    if abs(ox - (wx + 0.5)) + abs(oy - (wy + 0.5)) > 0.6), default=9)
        key = (hits, -near)
        if best is None or key < best[0]:
            best = (key, t_, h_)
(_hits, _near), w_tail, w_head = best
arrow(w_tail, w_head)
print(f"work arrow: {w_tail} -> ({w_head[0]:.2f}, {w_head[1]:.2f}); emojis hit {_hits}, clearance {-_near:.2f}")

c = BY_TITLE["Disclosure about Bob"]
x, y = highlight(c["model"], c["seed"], c["employee"])
arrow((x + 0.5, y), (x + 0.5, ext["Disclosure about Bob"][3]))

# ── example transcript panel, left of the grid ──
import pathlib
CHAT_PATH = pathlib.Path(str(REPO) + "/notes/example-chat.txt")
CHAT_MODEL, CHAT_SEED = "dspro", "s7"
MSG_RE = re.compile(r"^(\d{1,2}:\d{2})\s*([^:]+):\s*(.*)$", re.S)


def hide_brackets(text):
    """Replace every top-level [..] passage with '[...]'."""
    out, depth = [], 0
    for ch in text:
        if ch == "[":
            if depth == 0:
                out.append("[...]")
            depth += 1
        elif ch == "]" and depth:
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def parse_chat(path):
    """-> list of ("msg", time, speaker, text) / ("gap",); a fully [..]-wrapped message (or a run of
    them) becomes one gap row."""
    items = []
    for block in re.split(r"\n\s*\n", path.read_text()):
        block = "\n".join(l for l in block.strip().splitlines() if not l.startswith("%")).strip()
        if not block:
            continue
        if block.startswith("[") and hide_brackets(block).strip() == "[...]":
            if not items or items[-1][0] != "gap":
                items.append(("gap",))
            continue
        m = MSG_RE.match(block)
        if not m:
            raise ValueError(f"cannot parse chat message: {block[:60]!r}")
        text = re.sub(r"\s+", " ", hide_brackets(m.group(3))).strip()
        text = re.sub(r"\s+([,.;:])", r"\1", text)
        items.append(("msg", m.group(1), m.group(2).strip(), text))
    return items


CHAT_AVATAR = {"Priya": ("A", EMP_COLOR["Priya"], "white"), "Nadia": ("C", EMP_COLOR["Nadia"], "white"),
               "Matthieu": ("B", "#8a8a8a", "white"), "Rafael": ("D", "#88CCEE", "#1d1c1d")}
EMOJI_BOT = render_emoji("\U0001F4E2")  # loudspeaker


def chat_avatar(speaker, sc):
    a = AVATAR * sc
    if speaker not in CHAT_AVATAR:
        return VPacker(children=[OffsetImage(EMOJI_BOT, zoom=a / EMOJI_BOT.width)], align="top", pad=0, sep=0)
    letter, bg, fg = CHAT_AVATAR[speaker]
    da = DrawingArea(a, a, clip=False)
    da.add_artist(FancyBboxPatch((0, 0), a, a, boxstyle="round,pad=0,rounding_size=4",
                                 facecolor=bg, edgecolor="none"))
    da.add_artist(plt.Text(a / 2, a / 2 - 0.5, letter, ha="center", va="center",
                           fontsize=FS * 10 * sc, fontweight="bold", color=fg))
    return VPacker(children=[da], align="top", pad=0, sep=0)


def make_chat_box(width_pts, extra_h_pts=0, sc=1.0):
    feed_sep = 5 * FS * sc
    inner_w = width_pts - 2 * CARD_PAD - 2
    text_w = inner_w - AVATAR * sc - HSEP * sc
    rows = []
    for it in parse_chat(CHAT_PATH):
        if it[0] == "gap":
            # zero-height row: the ellipsis sits in the gutter between neighbouring messages
            da = DrawingArea(AVATAR * sc, 0, clip=False)
            da.add_artist(plt.Text(AVATAR * sc / 2, 0, "\u22ef", ha="center", va="center",
                                   fontsize=FS * 11 * sc, color="#999999"))
            rows.append(da)
            continue
        _, time, speaker, text = it
        name = HPacker(children=[
            TextArea(pseudonymize(speaker), textprops=dict(fontsize=FS * 8.5 * sc, fontweight="bold", color="#1d1c1d")),
            TextArea(time, textprops=dict(fontsize=FS * 8 * sc, color="#8a8a8a")),
        ], align="baseline", pad=0, sep=5 * sc)
        right = VPacker(children=[name, body_text(text, text_w, color="#1d1c1d", scale=sc), DrawingArea(text_w, 0)],
                        align="left", pad=0, sep=3 * sc)
        rows.append(HPacker(children=[chat_avatar(speaker, sc), right], align="top", pad=0, sep=HSEP * sc))
    if extra_h_pts > feed_sep:
        rows.append(DrawingArea(1, extra_h_pts - feed_sep))
    feed = card(VPacker(children=rows, align="left", pad=0, sep=feed_sep), facecolor="#ffffff", edgecolor="#cfcfcf")
    title = VPacker(children=[TextArea("Example conversation",
                                       textprops=dict(fontsize=FS * 12, fontweight="bold", color="#222222")),
                              DrawingArea(width_pts, 0)], align="left", pad=0, sep=0)
    return VPacker(children=[title, feed], align="left", pad=0, sep=4)


def add_chat(width_pts, extra_h_pts=0, sc=1.0):
    ab = AnnotationBbox(make_chat_box(width_pts, extra_h_pts, sc), (X0 - GAP, TOP), xycoords="data",
                        box_alignment=(1, 1), frameon=True, pad=0.4, annotation_clip=False, zorder=8,
                        bboxprops=dict(boxstyle="round,pad=0.35,rounding_size=0.6", facecolor="#fffdf7",
                                       edgecolor="#999999", linewidth=1.0))
    ax.add_artist(ab)
    snap(ab, (1, 1), (X0 - GAP, TOP))
    fig.canvas.draw()
    return ab


col_bottom = min(e[1] for e in ext.values())
chat_w = RIGHT_W  # same width as the right column; the transcript's text shrinks to fit the height
for chat_sc in [x / 200 for x in range(200, 80, -1)]:
    chat = add_chat(chat_w, sc=chat_sc)
    if data_extent(chat)[1] >= col_bottom:
        break
    chat.remove()
extra = 0.0
for _ in range(4):
    diff = (data_extent(chat)[1] - col_bottom) * PTS_PER_UNIT
    if abs(diff) < 0.3:
        break
    extra = max(0.0, extra + diff)
    chat.remove()
    chat = add_chat(chat_w, extra_h_pts=extra, sc=chat_sc)
boxes["chat"] = chat
ext = {t: data_extent(ab) for t, ab in boxes.items()}
print(f"chat width {chat_w} pts, text scale {chat_sc:.2f}")

# both dspro s7 seats, outlined together; one arrow to the transcript
cx0, cy_top = cell_box(CHAT_MODEL, CHAT_SEED, "Priya")
_, cy_bot = cell_box(CHAT_MODEL, CHAT_SEED, "Nadia")
ax.add_patch(Rectangle((cx0 + gap / 2, cy_bot + gap / 2), cell - gap, 2 * cell - gap,
                       facecolor="none", edgecolor=ARROW_COLOR, linewidth=2.6, zorder=6))
arrow((cx0, cy_top), (ext["chat"][2], cy_top + 1.0))

# disclosure markers in the top-right corner of the two disclosure cards
for title, icon in (("Disclosure about work", EMOJI_BRIEFCASE), ("Disclosure about Bob", EMOJI_MAN)):
    x0_, y0_, x1_, y1_ = ext[title]
    # sits on the frame's corner like a badge, clear of the heading text
    ax.add_artist(AnnotationBbox(OffsetImage(icon, zoom=FS * 13 / icon.height), (x1_ - 0.1, y1_ + 0.05),
                                 xycoords="data", box_alignment=(0.5, 0.5), frameon=False, pad=0,
                                 annotation_clip=False, zorder=9))

# pointers that teach the colour code on two concrete cells
POINTERS = [
    # (model, seed, employee, line 1, (line 2 plain, line 2 coloured, colour), label right edge x)
    ("deepseek", "s9", "Priya", "this run of Alice's agent", ("used misleading ", "framing", COLORS["FRAM"]), 1.6),
    ("deepseek", "s3", "Nadia", "this run of Carol's agent", ("made a ", "false claim", COLORS["FABR"]), n_cols),
]
PTR_TEXT = dict(fontsize=FS * 9, color="#444444")
fig.canvas.draw()
header_top = data_extent(runs_label)[3]
for model, seed, emp, line1, (plain, word, wcolor), lx in POINTERS:
    px, py = cell_box(model, seed, emp)
    line2 = HPacker(children=[TextArea(plain, textprops=PTR_TEXT),
                              TextArea(word, textprops={**PTR_TEXT, "color": wcolor, "fontweight": "bold"})],
                    align="baseline", pad=0, sep=0)
    lab = AnnotationBbox(VPacker(children=[TextArea(line1, textprops=PTR_TEXT), line2], align="right", pad=0, sep=2),
                         (lx, TOP - 0.05), xycoords="data", box_alignment=(1, 1), frameon=False, pad=0,
                         annotation_clip=False, zorder=9)
    ax.add_artist(lab)
    fig.canvas.draw()
    le = data_extent(lab)
    if le[1] < header_top + 0.1 and le[0] < data_extent(runs_label)[2] and le[2] > data_extent(runs_label)[0]:
        print("WARNING: pointer label collides with the runs header")
    ax.add_patch(Rectangle((px + gap / 2, py + gap / 2), cell - gap, cell - gap,
                           facecolor="none", edgecolor="#444444", linewidth=2.2, zorder=6))
    arrow((px + 0.5, le[1] - 0.05), (px + 0.5, py + 0.72), color="#555555")

# ── legend: full-width strip under both columns ──
all_x0 = min(e[0] for e in ext.values())
all_x1 = max(e[2] for e in ext.values())
bottom = min(e[1] for e in ext.values())
center_x = (all_x0 + all_x1) / 2
leg = ax.legend(loc="upper center", bbox_to_anchor=(center_x, bottom - 0.25),
                **{**LEGEND_KW, "ncol": 6, "columnspacing": 1.6})
fig.canvas.draw()
_, ly0, _, _ = data_extent(leg)
emoji_y = ly0 - 0.4 * FS
for icon, dx, label in [(EMOJI_MAN, -8.0, "Direct disclosure, about Bob"),
                        (EMOJI_BRIEFCASE, 1.5, "Direct disclosure, about work")]:
    ax.add_artist(AnnotationBbox(OffsetImage(icon, zoom=0.09 * FS), (center_x + dx, emoji_y),
                                 xycoords="data", frameon=False, pad=0, annotation_clip=False))
    ax.text(center_x + dx + 0.75, emoji_y, label, ha="left", va="center", fontsize=FS * 9, color="#333333")

fig.patch.set_facecolor("#fcfcfb")
ax.set_facecolor("#fcfcfb")

out = str(REPO) + "/experiments/agent5/findings/full_image.png"
plt.savefig(out, facecolor=fig.get_facecolor())

# the full-figure axes defeats bbox_inches="tight", so crop to the drawn content instead
im = Image.open(out).convert("RGB")
bg = Image.new("RGB", im.size, (0xfc, 0xfc, 0xfb))
from PIL import ImageChops
x0, y0, x1, y1 = ImageChops.difference(im, bg).getbbox()
m = 50
im.crop((max(x0 - m, 0), max(y0 - m, 0), min(x1 + m, im.width), min(y1 + m, im.height))).save(out)
print("saved", out)
