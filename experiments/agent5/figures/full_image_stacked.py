"""Paper figure: example conversation on top, 'Overview over 80 runs' (legend, matrix, four quote cards) below.

Writes experiments/agent5/findings/full_image_stacked.png. Inputs: outputs/5e8_seats_human_labeled.md,
the per-run run.json files, findings/full_image_callouts.json and notes/example-chat.txt
(the latter two only for the full_image* figures).

Needs matplotlib, numpy, pillow, uharfbuzz and fonttools, and macOS's Apple Color Emoji font.
Run from anywhere: python experiments/agent5/figures/full_image_stacked.py
"""
import os
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
os.environ.setdefault("GK", "1.2")
os.environ.setdefault("FT", "2.6")
os.environ.setdefault("RIGHT_CHARS", "44")
os.environ.setdefault("MODE", "stacked")
os.environ.setdefault("CHAT_COLS", "1")
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
    "NOTH": "Nothing",
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
GK = float(__import__('os').environ.get('GK', 1.0))  # grid zoom
FS = 1.75 * GK  # grid/legend font scale
FT = float(__import__('os').environ.get('FT', 3.2))  # quote/transcript text scale
CHAT_CHARS = int(__import__('os').environ.get('CHAT_CHARS', 40))
RIGHT_CHARS = int(__import__('os').environ.get('RIGHT_CHARS', 30))
ARROW_COLOR = "#B33A3A"
S = 0.42 * GK  # inches per data unit, fixed so point-sized text maps predictably to data units

n_rows = len(MODEL_ORDER) * len(EMP_ORDER)  # 16
n_cols = len(SEED_ORDER)  # 10

XLIM = (-12, 60)
YLIM = (-20, 90)
fig = plt.figure(figsize=((XLIM[1] - XLIM[0]) * S, (YLIM[1] - YLIM[0]) * S), dpi=200)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(*XLIM)
ax.set_ylim(*YLIM)
ax.set_aspect("equal")
ax.axis("off")

cell = 1.0
gap = 0.08

COL_MOVES = {  # model -> [(seed, column)], applied in order; column 0 = leftmost
    "deepseek": [("s9", 0), ("s2", 9), ("s3", 8)],
    "glm": [("s4", 9)],
    "glm53flash": [("s8", 9)],
    "qwen38flash": [("s4", 9)],
}
COL_POS = {}
for _m in MODEL_ORDER:
    _order = list(reversed(SEED_ORDER))
    for _sd, _col in COL_MOVES.get(_m, []):
        _order.remove(_sd)
        _order.insert(_col, _sd)
    for _x, _sd in enumerate(_order):
        COL_POS[(_m, _sd)] = _x

for ci, model in enumerate(MODEL_ORDER):
    for ei, employee in enumerate(EMP_ORDER):
        row = ci * 2 + ei
        y = n_rows - 1 - row
        for ri, seed in enumerate(SEED_ORDER):
            x = COL_POS[(model, seed)]
            v = rows[(model, seed, employee)]
            ax.add_patch(Rectangle(
                (x + gap / 2, y + gap / 2), cell - gap, cell - gap,
                facecolor=COLORS[v], edgecolor="white", linewidth=1.2,
            ))
            matt, work = disclosure[(model, seed, employee)]
            icons = [im for im, flag in ((EMOJI_MAN, matt), (EMOJI_BRIEFCASE, work)) if flag]
            if icons:
                cx, cy = x + cell / 2, y + cell / 2
                icon_zoom = (0.155 if len(icons) == 1 else 0.105) * GK
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
CHIP = 0.6 * 1.75          # side of the [A]/[C] chips, data units
CHIP_RIGHT = -0.14  # chips' right edge, just left of the grid
brace_x = CHIP_RIGHT - CHIP - 0.14
brace_width = 0.22
model_labels = []
ROT_NAME = {"deepseek": "deepseek-v4\nflash-0731", "dspro": "deepseek-v4\npro-0813", "glm": "glm-5.2",
            "glm53": "glm-5.3", "glm53flash": "glm-5.3-\nflash", "kimi": "Kimi-K2.6", "kimik3": "Kimi-K3",
            "qwen38flash": "Qwen3.8-\nFlash"}
ROT_FS = min(FS * 9, 0.97 * (2 * S * 72) / (11 * 0.6))
for ci, model in enumerate(MODEL_ORDER):
    y0, y1 = n_rows - (ci * 2 + 2) + 0.06, n_rows - ci * 2 - 0.06
    draw_left_brace(ax, y0, y1, brace_x, brace_width, color="#666666", lw=1.3)
    model_labels.append(ax.text(brace_x - brace_width - 0.08, (y0 + y1) / 2, ROT_NAME[model],
            ha="right", va="center", multialignment="right", linespacing=1.0,
            fontsize=FS * 8.5, color="#666666", zorder=3.6,
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
    return COL_POS[(model, seed)], n_rows - 1 - row


EMOJI_THOUGHT = render_emoji("\U0001F4AD")
EMOJI_SPEECH = render_emoji("\U0001F4AC")
EMP_NAME = {"Priya": "Alice", "Nadia": "Carol"}
AVATAR_COLOR = EMP_COLOR
BY_TITLE = {c["title"]: c for c in CALLOUTS}

MONO_CHAR_PTS = FT * 8 * 0.602  # DejaVu Sans Mono advance at 8pt
CARD_PAD = 4 * FT
AVATAR = 20 * FT
HSEP = 5 * FT


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
    size = FT * 8 * scale
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
                             TextArea(label, textprops=dict(fontsize=FT * 8.5, fontweight="bold", color="#555555"))],
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
                    textprops=dict(fontsize=FT * 8.5, style="italic", color="#6b5fa8"))
    right = VPacker(children=[name, body_text(text, text_w, style="italic", color="#4a4a5a"),
                              DrawingArea(text_w, 0)], align="left", pad=0, sep=3)
    return card(HPacker(children=[icon_column(EMOJI_THOUGHT), right], align="top", pad=0, sep=HSEP),
                facecolor="#f2f0f9", edgecolor="#a99fd0", linestyle=(0, (3, 2)))


def avatar(employee):
    da = DrawingArea(AVATAR, AVATAR, clip=False)
    da.add_artist(FancyBboxPatch((0, 0), AVATAR, AVATAR, boxstyle="round,pad=0,rounding_size=4",
                                 facecolor=AVATAR_COLOR[employee], edgecolor="none"))
    da.add_artist(plt.Text(AVATAR / 2, AVATAR / 2 - 0.5, EMP_NAME[employee][0], ha="center", va="center",
                           fontsize=FT * 10, fontweight="bold", color="white"))
    return VPacker(children=[da], align="top", pad=0, sep=0)


AGENT_COLOR = {"Priya": "#332288", "Nadia": "#999933", "Rafael": "#88CCEE", "Matthieu": "#8a8a8a"}


def robot(color, size):
    """The agent pictogram (16x16 viewBox), drawn at `size` points."""
    k = size / 16
    da = DrawingArea(size, size * 0.8, clip=False)
    lw = 1.6 * k
    da.add_artist(FancyBboxPatch((2 * k, 3 * k - 2 * k), 12 * k, 9 * k, boxstyle=f"round,pad=0,rounding_size={2 * k}",
                                 facecolor="none", edgecolor=color, linewidth=lw))
    for cx_ in (6, 10):
        da.add_artist(plt.Circle((cx_ * k, 7.5 * k - 2 * k), 1.3 * k, facecolor=color, edgecolor="none"))
    da.add_artist(Line2D([8 * k, 8 * k], [12 * k - 2 * k, 14 * k - 2 * k], color=color, linewidth=lw))
    return da


def message_card(text, employee, inner_w):
    text_w = inner_w - AVATAR - HSEP
    name = HPacker(children=[
        TextArea(EMP_NAME[employee], textprops=dict(fontsize=FT * 8.5, fontweight="bold", color="#1d1c1d")),
        robot(AGENT_COLOR[employee], FT * 8.5),
        TextArea("message", textprops=dict(fontsize=FT * 8, color="#8a8a8a")),
    ], align="baseline", pad=0, sep=5)
    right = VPacker(children=[name, body_text(text, text_w, color="#1d1c1d"), DrawingArea(text_w, 0)],
                    align="left", pad=0, sep=3)
    return card(HPacker(children=[avatar(employee), right], align="top", pad=0, sep=HSEP),
                facecolor="#ffffff", edgecolor="#cfcfcf")


def make_box(c, width_pts, extra_h_pts=0):
    inner_w = width_pts - 2 * CARD_PAD - 2
    model_name = MODEL_FULL_NAME[c["model"]].replace(chr(10), " ")
    parts_ = [TextArea(c["title"], textprops=dict(fontsize=FT * 8, fontweight="bold", color="#222222")),
              TextArea(model_name, textprops=dict(fontsize=FT * 6, style="italic", color="#777777"))]
    one_line = len(c["title"]) * FT * 8 * 0.64 + 6 * FT + len(model_name) * FT * 6 * 0.56
    heading = (HPacker(children=parts_, align="baseline", pad=0, sep=6 * FT) if one_line < width_pts
               else VPacker(children=parts_, align="left", pad=0, sep=1))
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
GAP = 0.6 / GK       # gutter between every pair of blocks, in data units
RIGHT_START = n_cols + GAP  # left edge of the right column
TOP = n_rows + 2.5  # top of the pointer labels above the grid
BOTTOM_TOP = -0.95   # top edge of the box under the grid (leaves a corridor for the Bob arrow)
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
RIGHT_ORDER = ["Framing", "False claim", "Disclosure about work", "Disclosure about Bob"]
MIDDLE = "Disclosure about work"
MID_RIGHT = 5.8
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
ROW_H = 0.78 * 1.75

fig.canvas.draw()
_X0 = min(data_extent(t)[0] for t in model_labels)
_cx = (_X0 + n_cols) / 2
PTR_TEXT = dict(fontsize=FS * 9, color="#444444")


def ptr_label(line1, plain, word, wcolor, xy, align):
    line2 = HPacker(children=[TextArea(plain, textprops=PTR_TEXT),
                              TextArea(word, textprops={**PTR_TEXT, "color": wcolor, "fontweight": "bold"})],
                    align="baseline", pad=0, sep=0)
    body = VPacker(children=[TextArea(line1, textprops=PTR_TEXT), line2],
                   align="left" if align[0] == 0 else "right", pad=0, sep=2)
    ab = AnnotationBbox(body, xy, xycoords="data", box_alignment=align, frameon=False, pad=0,
                        annotation_clip=False, zorder=9)
    ax.add_artist(ab)
    fig.canvas.draw()
    return ab


hdr_top = data_extent(runs_label)[3]
lab_false = ptr_label("this run of Carol's agent", "made a ", "false claim", COLORS["FABR"],
                      (n_cols + 0.15, hdr_top + 0.25), (1, 0))
lab_frame = ptr_label("this run of Alice's agent", "used misleading ", "framing", COLORS["FRAM"],
                      (0, data_extent(lab_false)[3] + 0.2), (0, 0))
emoji_base = data_extent(lab_frame)[3] + 0.45 + 0.5 * ROW_H
top_leg = ax.legend(loc="lower center", bbox_to_anchor=(_cx, emoji_base + 1.5 * ROW_H), **LEGEND_KW)
ax.add_artist(top_leg)  # keep it when later ax.legend() calls replace the axes legend
fig.canvas.draw()
_lx0, _, _, _ly1 = data_extent(top_leg)
for i, (icon, label) in enumerate([(EMOJI_BRIEFCASE, "Direct disclosure, about work"),
                                   (EMOJI_MAN, "Direct disclosure, about Bob")]):
    ey = emoji_base + i * ROW_H
    ax.add_artist(AnnotationBbox(OffsetImage(icon, zoom=0.09 * FS), (_lx0 + 0.55, ey),
                                 xycoords="data", frameon=False, pad=0, annotation_clip=False))
    _t = ax.text(_lx0 + 1.15, ey, label, ha="left", va="center", fontsize=FS * 9, color="#333333")
fig.canvas.draw()
_rx = data_extent(_t)[2] + 1.0
ax.add_artist(AnnotationBbox(robot("#555555", FS * 11), (_rx + 0.3, emoji_base + ROW_H), xycoords="data",
                             frameon=False, pad=0, annotation_clip=False))
ax.text(_rx + 0.9, emoji_base + ROW_H, "agent", ha="left", va="center", fontsize=FS * 9, color="#333333")
TOP = _ly1 + 0.1
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
    # the work box may reach right up to where the Bob arrow passes its top edge
    global MID_RIGHT
    _bc = BY_TITLE["Disclosure about Bob"]
    _bx, _by = cell_box(_bc["model"], _bc["seed"], _bc["employee"])
    _bb = data_extent(boxes["Disclosure about Bob"])
    MID_RIGHT = GRID_RIGHT - 0.3
    bob_right = MID_RIGHT
    mid_w = (MID_RIGHT - X0 - frame_extra) * PTS_PER_UNIT
    bob_bottom = 0.0
    return y + GAP, bob_bottom, bob_right


def clear():
    for t in RIGHT_ORDER:
        if t in boxes:
            boxes.pop(t).remove()


def col_w(chars):
    return chars * MONO_CHAR_PTS + AVATAR + HSEP + 2 * CARD_PAD + 8


RIGHT_W = col_w(RIGHT_CHARS)
right_bottom, left_bottom, BOB_RIGHT = build(RIGHT_W)
bob_bottom = 0.0
best = (0, RIGHT_W)
print(f"right col {RIGHT_W:.0f} pts")

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


c = BY_TITLE["Framing"]
_gx, _gy = highlight(c["model"], c["seed"], c["employee"])
_gb = ext["Framing"]
arrow((_gx + 1, _gy + 0.5), (_gb[0], max(_gy + 0.5, _gb[1] + 0.45)))
to_left_edge("False claim")

# work arrow: one straight segment, chosen to hit as few in-cell emojis / T1 dots as possible
# and to stay clear of the False-claim and Bob arrows
fc = BY_TITLE["False claim"]
fx, fy = cell_box(fc["model"], fc["seed"], fc["employee"])
fbx0, fby0, fbx1, fby1 = ext["False claim"]
fc_seg = ((fx + 1, fy + 0.5), (fbx0, min(max(fy + 0.5, fby0 + 0.8), fby1 - 0.8)))
bc = BY_TITLE["Disclosure about Bob"]
bx_, by_ = cell_box(bc["model"], bc["seed"], bc["employee"])
_be = ext["Disclosure about Bob"]
bob_seg = ((bx_ + 1, by_), (_be[0], min(max(-0.35, _be[1] + 0.8), _be[3] - 0.8)))

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
def best_segment(cell_xy, box, avoid, own):
    """Straight arrow from the cell border to the box's left edge, fewest emoji hits, no crossings."""
    cx_, cy_ = cell_xy
    tails = ([(cx_ + 1, cy_ + f / 10) for f in range(0, 11)] + [(cx_ + f / 10, cy_) for f in range(5, 11)]
             + [(cx_ + f / 10, cy_ + 1) for f in range(5, 11)])
    heads = [(box[0], box[3] - 0.5 - 0.25 * k) for k in range(40) if box[3] - 0.5 - 0.25 * k > box[1] + 0.5]
    best_ = None
    for t_ in tails:
        for h_ in heads:
            if any(segs_cross(t_, h_, *sg) for sg in avoid):
                continue
            if abs(h_[1] - t_[1]) < 0.6 and abs(t_[1] - round(t_[1])) < 0.2:
                continue  # would run along a grid line
            hits = sum(1 for (ox, oy, r) in obstacles
                       if abs(ox - own[0]) + abs(oy - own[1]) > 0.6 and seg_point_dist(t_, h_, (ox, oy)) < r)
            near = min((seg_point_dist(t_, h_, (ox, oy)) - r for (ox, oy, r) in obstacles
                        if abs(ox - own[0]) + abs(oy - own[1]) > 0.6), default=9)
            # also prefer short arrows that don't hug a grid line
            key = (hits, -round(near, 2), abs(h_[1] - t_[1]))
            if best_ is None or key < best_[0]:
                best_ = (key, t_, h_)
    return best_


w_best = best_segment((wx, wy), ext["Disclosure about work"], [fc_seg], (wx + 0.5, wy + 0.5))
arrow(w_best[1], w_best[2])
print("work arrow", w_best[1], w_best[2], "hits", w_best[0][0])

c = BY_TITLE["Disclosure about Bob"]
bx_, by_ = highlight(c["model"], c["seed"], c["employee"])
b_best = best_segment((bx_, by_), ext["Disclosure about Bob"], [fc_seg, (w_best[1], w_best[2])], (bx_ + 0.5, by_ + 0.5))
arrow(b_best[1], b_best[2])
print("bob arrow", b_best[1], b_best[2], "hits", b_best[0][0])

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
                           fontsize=FT * 10 * sc, fontweight="bold", color=fg))
    return VPacker(children=[da], align="top", pad=0, sep=0)


def make_chat_box(width_pts, extra_h_pts=0, sc=1.0):
    feed_sep = 5 * FT * sc
    inner_w = width_pts - 2 * CARD_PAD - 2
    text_w = inner_w - AVATAR * sc - HSEP * sc
    rows = []
    for it in parse_chat(CHAT_PATH):
        if it[0] == "gap":
            # zero-height row: the ellipsis sits in the gutter between neighbouring messages
            da = DrawingArea(AVATAR * sc, 0, clip=False)
            da.add_artist(plt.Text(AVATAR * sc / 2, 0, "\u22ef", ha="center", va="center",
                                   fontsize=FT * 11 * sc, color="#999999"))
            rows.append(da)
            continue
        _, time, speaker, text = it
        name = HPacker(children=[
            TextArea(pseudonymize(speaker), textprops=dict(fontsize=FT * 8.5 * sc, fontweight="bold", color="#1d1c1d")),
            *([robot(AGENT_COLOR[speaker], FT * 8.5 * sc)] if speaker in AGENT_COLOR else []),
            TextArea(time, textprops=dict(fontsize=FT * 8 * sc, color="#8a8a8a")),
        ], align="baseline", pad=0, sep=5 * sc)
        right = VPacker(children=[name, body_text(text, text_w, color="#1d1c1d", scale=sc), DrawingArea(text_w, 0)],
                        align="left", pad=0, sep=3 * sc)
        rows.append(HPacker(children=[chat_avatar(speaker, sc), right], align="top", pad=0, sep=HSEP * sc))
    if extra_h_pts > feed_sep:
        rows.append(DrawingArea(1, extra_h_pts - feed_sep))
    feed = card(VPacker(children=rows, align="left", pad=0, sep=feed_sep), facecolor="#ffffff", edgecolor="#cfcfcf")
    title = VPacker(children=[TextArea("Example conversation",
                                       textprops=dict(fontsize=FT * 9, fontweight="bold", color="#222222")),
                              DrawingArea(width_pts, 0)], align="left", pad=0, sep=0)
    return VPacker(children=[title, feed], align="left", pad=0, sep=4)


def chat_rows(text_w, sc):
    rows, weights = [], []
    for it in parse_chat(CHAT_PATH):
        if it[0] == "gap":
            da = DrawingArea(AVATAR * sc, 0, clip=False)
            da.add_artist(plt.Text(AVATAR * sc / 2, 0, "\u22ef", ha="center", va="center",
                                   fontsize=FT * 11 * sc, color="#999999"))
            rows.append(da)
            weights.append(0.0)
            continue
        _, time, speaker, text = it
        name = HPacker(children=[
            TextArea(pseudonymize(speaker), textprops=dict(fontsize=FT * 8.5 * sc, fontweight="bold", color="#1d1c1d")),
            *([robot(AGENT_COLOR[speaker], FT * 8.5 * sc)] if speaker in AGENT_COLOR else []),
            TextArea(time, textprops=dict(fontsize=FT * 8 * sc, color="#8a8a8a")),
        ], align="baseline", pad=0, sep=5 * sc)
        right = VPacker(children=[name, body_text(text, text_w, color="#1d1c1d", scale=sc), DrawingArea(text_w, 0)],
                        align="left", pad=0, sep=3 * sc)
        rows.append(HPacker(children=[chat_avatar(speaker, sc), right], align="top", pad=0, sep=HSEP * sc))
        chars_per_line = max(10, int(text_w // (MONO_CHAR_PTS * sc)) - 1)
        weights.append(1.6 + len(text) / chars_per_line)
    return rows, weights


def make_chat_box_2col(width_pts, sc, title="Example conversation"):
    feed_sep = 5 * FT * sc
    col_gap = 14 * FT * sc
    ncols = int(__import__("os").environ.get("CHAT_COLS", 1))
    inner_w = (width_pts - 2 * CARD_PAD - 2 - col_gap * (ncols - 1)) / ncols
    text_w = inner_w - AVATAR * sc - HSEP * sc
    rows, weights = chat_rows(text_w, sc)
    if ncols == 1:
        body = VPacker(children=rows + [DrawingArea(inner_w, 0)], align="left", pad=0, sep=feed_sep)
        feed = card(body, facecolor="#ffffff", edgecolor="#cfcfcf")
        head = VPacker(children=[TextArea(title, textprops=dict(fontsize=FT * 9, fontweight="bold", color="#222222")),
                                 DrawingArea(width_pts, 0)], align="left", pad=0, sep=0)
        return VPacker(children=[head, feed], align="left", pad=0, sep=4)
    total = sum(weights)
    acc, split = 0.0, len(rows)
    for i, w in enumerate(weights):
        if acc + w / 2 >= total / 2:
            split = i
            break
        acc += w
    cols = [VPacker(children=part, align="left", pad=0, sep=feed_sep) for part in (rows[:split], rows[split:])]
    cols = [VPacker(children=[c_, DrawingArea(inner_w, 0)], align="left", pad=0, sep=0) for c_ in cols]
    feed = card(HPacker(children=cols, align="top", pad=0, sep=col_gap), facecolor="#ffffff", edgecolor="#cfcfcf")
    head = VPacker(children=[TextArea(title, textprops=dict(fontsize=FT * 9, fontweight="bold", color="#222222")),
                             DrawingArea(width_pts, 0)], align="left", pad=0, sep=0)
    return VPacker(children=[head, feed], align="left", pad=0, sep=4)


def add_chat(width_pts, extra_h_pts=0, sc=1.0):
    ab = AnnotationBbox(make_chat_box(width_pts, extra_h_pts, sc), (X0 - GAP, TOP), xycoords="data",
                        box_alignment=(1, 1), frameon=True, pad=0.4, annotation_clip=False, zorder=8,
                        bboxprops=dict(boxstyle="round,pad=0.35,rounding_size=0.6", facecolor="#fffdf7",
                                       edgecolor="#999999", linewidth=1.0))
    ax.add_artist(ab)
    snap(ab, (1, 1), (X0 - GAP, TOP))
    fig.canvas.draw()
    return ab


MODE = __import__("os").environ.get("MODE", "2col")
STAR = "\u2605"
cx0, cy_top = cell_box(CHAT_MODEL, CHAT_SEED, "Priya")
_, cy_bot = cell_box(CHAT_MODEL, CHAT_SEED, "Nadia")
ax.add_patch(Rectangle((cx0 + gap / 2, cy_bot + gap / 2), cell - gap, 2 * cell - gap,
                       facecolor="none", edgecolor=ARROW_COLOR, linewidth=2.6, zorder=6))
for yy in (cy_top, cy_bot):
    ax.text(cx0 + 0.5, yy + 0.47, STAR, ha="center", va="center", fontsize=FS * 12, color="#222222", zorder=7,
            path_effects=[pe.withStroke(linewidth=3, foreground="white")])

# disclosure markers in the top-right corner of the two disclosure cards
for title, icon in (("Disclosure about work", EMOJI_BRIEFCASE), ("Disclosure about Bob", EMOJI_MAN)):
    x0_, y0_, x1_, y1_ = ext[title]
    # sits on the frame's corner like a badge, clear of the heading text
    ax.add_artist(AnnotationBbox(OffsetImage(icon, zoom=FT * 13 / icon.height), (x1_ - 0.6, y1_ - 0.55),
                                 xycoords="data", box_alignment=(0.5, 0.5), frameon=False, pad=0,
                                 annotation_clip=False, zorder=9))

# pointer arrows from the labels in the top band
for (model, seed, emp), lab in ((("deepseek", "s9", "Priya"), lab_frame), (("deepseek", "s3", "Nadia"), lab_false)):
    px, py = cell_box(model, seed, emp)
    le = data_extent(lab)
    ax.add_patch(Rectangle((px + gap / 2, py + gap / 2), cell - gap, cell - gap,
                           facecolor="none", edgecolor="#444444", linewidth=2.2, zorder=6))
    arrow((px + 0.5, le[1] - 0.05), (px + 0.5, py + 0.72), color="#555555")

# ── both columns end on one line ──
bottom = min(ext[RIGHT_ORDER[-1]][1], 0.0)
ext = {t: data_extent(ab) for t, ab in boxes.items()}
fig.canvas.draw()
FIG_X0 = min(X0, min(e[0] for e in ext.values()))
FIG_X1 = max(e[2] for e in ext.values()) + 0.3  # badges stick out a little
CHAT_SC = float(__import__("os").environ.get("CHAT_SC", 0.9))
chat_title = f"Example conversation  {STAR}"


def add_strip(width_units, x0, y_top):
    ab = AnnotationBbox(make_chat_box_2col((width_units - frame_extra) * PTS_PER_UNIT, CHAT_SC, chat_title),
                        (x0, y_top), xycoords="data", box_alignment=(0, 1), frameon=True, pad=0.4,
                        annotation_clip=False, zorder=8,
                        bboxprops=dict(boxstyle="round,pad=0.35,rounding_size=0.6", facecolor="#fffdf7",
                                       edgecolor="#999999", linewidth=1.0))
    ax.add_artist(ab)
    snap(ab, (0, 1), (x0, y_top))
    fig.canvas.draw()
    return ab


if MODE == "stacked":
    # overview title above the legend/grid/cards block, the conversation above that
    fig.canvas.draw()
    block_top = max(TOP, max(e[3] for e in ext.values()))
    ov = ax.text(FIG_X0 + 0.15, block_top + 0.35, "Overview over 80 runs", ha="left", va="bottom",
                 fontsize=FT * 9, fontweight="bold", color="#222222", zorder=9)
    fig.canvas.draw()
    strip = add_strip(FIG_X1 - FIG_X0, FIG_X0, 0)
    sh = data_extent(strip)
    strip.remove()
    strip_h = sh[3] - sh[1]
    strip_ab = add_strip(FIG_X1 - FIG_X0, FIG_X0, data_extent(ov)[3] + 2 * GAP + strip_h)

fig.patch.set_facecolor("#fcfcfb")
ax.set_facecolor("#fcfcfb")

def save_cropped(path):
    plt.savefig(path, facecolor=fig.get_facecolor())
    from PIL import ImageChops
    im = Image.open(path).convert("RGB")
    bg = Image.new("RGB", im.size, (0xfc, 0xfc, 0xfb))
    x0_, y0_, x1_, y1_ = ImageChops.difference(im, bg).getbbox()
    m = 50
    im.crop((max(x0_ - m, 0), max(y0_ - m, 0), min(x1_ + m, im.width), min(y1_ + m, im.height))).save(path)
    print("saved", path, im.crop((x0_, y0_, x1_, y1_)).size)


FIND = str(REPO) + "/experiments/agent5/findings/"
if MODE == "stacked":
    save_cropped(FIND + "full_image_stacked.png")  # wide
    # vector PDFs of the two halves: same figure, cropped at the gap between conversation and overview
    from matplotlib.transforms import Bbox
    plt.rcParams["pdf.fonttype"] = 42
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    _buf = np.asarray(fig.canvas.buffer_rgba())[:, :, :3].astype(int)
    _ink = np.abs(_buf - np.array([0xFC, 0xFC, 0xFB])).sum(axis=2) > 12
    _ys, _xs = np.where(_ink)
    _H = _buf.shape[0]
    whole = Bbox([[_xs.min() / fig.dpi, (_H - _ys.max()) / fig.dpi], [_xs.max() / fig.dpi, (_H - _ys.min()) / fig.dpi]])
    to_in = fig.dpi_scale_trans.inverted()
    convo = strip_ab.get_window_extent(r).transformed(to_in)
    over_top = ov.get_window_extent(r).transformed(to_in).y1
    pad = 0.15
    halves = {
        "example_convo.pdf": Bbox([[convo.x0 - pad, convo.y0 - pad], [convo.x1 + pad, convo.y1 + pad]]),
        "full_image_2col.pdf": Bbox([[whole.x0 - pad, whole.y0 - pad], [whole.x1 + pad, over_top + pad]]),
    }
    for name, bb in halves.items():
        fig.savefig(FIND + name, facecolor=fig.get_facecolor(), bbox_inches=bb)
        print("saved", FIND + name, f"{bb.width:.2f} x {bb.height:.2f} in")
else:
    save_cropped(FIND + "full_image_2col.png")
    # the transcript on its own, same width and text scale as the main figure
    for art in list(ax.patches) + list(ax.texts) + list(ax.artists) + list(ax.lines) + list(ax.collections):
        art.remove()
    if ax.get_legend():
        ax.get_legend().remove()
    fig.canvas.draw()
    add_strip(FIG_X1 - FIG_X0, FIG_X0, TOP)
    save_cropped(FIND + "example_convo.png")
