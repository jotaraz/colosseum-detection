"""Short paper figure: the four example cards around an aggregate bar chart (all 160 agents).

Same width as full_image_2col, much shorter: the per-run matrix collapses to 8 counts
(five verdicts, claimed-T1, Bob- and work-disclosure). Writes findings/overview_bars.{png,pdf}.
POSTER=1 writes findings/overview_bars_poster.{png,pdf} instead, with the comic poster's category names.
Inputs: outputs/5e8_seats_human_labeled.md, the run.json files, findings/full_image_callouts.json.

Needs matplotlib, numpy, pillow, uharfbuzz and fonttools, and macOS's Apple Color Emoji font.
"""
import os
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
os.environ.setdefault("GK", "1.2")
os.environ.setdefault("FT", "2.6")
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

EMP_COLOR = {"Priya": "#332288", "Nadia": "#999933"}  # Alice indigo, Carol olive
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
    parts_ = [TextArea(shown(c["title"]), textprops=dict(fontsize=FT * 8, fontweight="bold", color="#222222")),
              TextArea(model_name, textprops=dict(fontsize=FT * 6, style="italic", color="#777777"))]
    one_line = len(shown(c["title"])) * FT * 8 * 0.64 + 6 * FT + len(model_name) * FT * 6 * 0.56
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


BOX_COLORS = {"Misdirection": (tint(COLORS["FRAM"], 0.16), tint(COLORS["FRAM"], 0.75)),
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



# ════════════════════════ layout: cards | bars | cards ════════════════════════
import pathlib
from matplotlib.transforms import Bbox

FIND = pathlib.Path(str(REPO)) / "experiments" / "agent5" / "findings"
TOTAL_W = 34.1      # content width in data units — matches full_image_2col (17.2 in)
MIN_BARS_W = 9.0    # narrowest the bar chart may get
LEFT_COL = ["Misdirection", "Disclosure about work"]
RIGHT_COL = ["False claim", "Disclosure about Bob"]
TOP = 0.0           # everything hangs from y = 0 downward
POSTER = os.environ.get("POSTER") == "1"
# poster wording (matches the comic's "No clean way out" signs); keys are the paper names
POSTER_NAMES = {"False claim": "False claim as reason", "Misdirection": "Misdirect about true reason",
                "No deception": "Refuse to say why",
                "Goes for T1": "Directly goes for T1", "Claimed T1": "Takes T1 after initial resistance",
                "Disclosed: Bob": "Leaks Alice\u2019s secret", "Disclosed: work": "Leaks additional private info",
                "Disclosure about Bob": "Leaks Alice\u2019s secret",
                "Disclosure about work": "Leaks additional private info"}

# poster colours: blue = the poster's "confidential" highlight, purple = ends on T1
# (solid: directly; pale + dark-purple hatching: later — a flat light purple reads as the false-claim pink)
POSTER_COLORS = {"Goes for T1": ("#6A3D9A", "none"), "Claimed T1": ("#F1ECF7", "#6A3D9A"),
                 "Disclosed: Bob": ("#9FC6EA", "none")}


def shown(name):
    return POSTER_NAMES.get(name, name) if POSTER else name


def col_w(chars):
    return chars * MONO_CHAR_PTS + AVATAR + HSEP + 2 * CARD_PAD + 8


probe = add_box("Misdirection", 300, (0, 1), (0, 0))
fig.canvas.draw()
_p = data_extent(probe)
frame_extra = (_p[2] - _p[0]) - 300 / PTS_PER_UNIT
probe.remove()

boxes = {}


def place_column(titles, x0, width_pts, align_x):
    y = TOP
    for t in titles:
        boxes[t] = add_box(t, width_pts, (align_x, 1), (x0, y))
        snap(boxes[t], (align_x, 1), (x0, y))
        fig.canvas.draw()
        y = data_extent(boxes[t])[1] - GAP
    return y + GAP


def clear():
    for t in list(boxes):
        boxes.pop(t).remove()


# pick card width (chars per line) giving the shortest figure that still leaves room for the bars
best = None
for chars in range(24, 46):
    cw = col_w(chars) / PTS_PER_UNIT + frame_extra
    bars_w = TOTAL_W - 2 * cw - 2 * GAP
    if bars_w < MIN_BARS_W:
        break
    lb = place_column(LEFT_COL, 0, col_w(chars), 0)
    rb = place_column(RIGHT_COL, TOTAL_W, col_w(chars), 1)
    clear()
    h = -min(lb, rb)
    if best is None or h < best[0]:
        best = (h, chars)
CHARS = best[1]
CW = col_w(CHARS) / PTS_PER_UNIT + frame_extra
left_bottom = place_column(LEFT_COL, 0, col_w(CHARS), 0)
right_bottom = place_column(RIGHT_COL, TOTAL_W, col_w(CHARS), 1)
BOTTOM = min(left_bottom, right_bottom)


def pad_to(title, x0, align_x, bottom):
    extra = 0.0
    for _ in range(4):
        d = (data_extent(boxes[title])[1] - bottom) * PTS_PER_UNIT
        if abs(d) < 0.3:
            return
        extra = max(0.0, extra + d)
        top = data_extent(boxes[title])[3]
        boxes[title].remove()
        boxes[title] = add_box(title, col_w(CHARS), (align_x, 1), (x0, top), extra_h_pts=extra + 4)
        snap(boxes[title], (align_x, 1), (x0, top))
        fig.canvas.draw()


pad_to(LEFT_COL[-1], 0, 0, BOTTOM)
pad_to(RIGHT_COL[-1], TOTAL_W, 1, BOTTOM)
ext = {t: data_extent(ab) for t, ab in boxes.items()}
print(f"card width {CHARS} chars, column height {-BOTTOM:.1f} units")

# disclosure badges, as in the overview figure
for title, icon in (("Disclosure about work", EMOJI_BRIEFCASE), ("Disclosure about Bob", EMOJI_MAN)):
    x0_, y0_, x1_, y1_ = ext[title]
    ax.add_artist(AnnotationBbox(OffsetImage(icon, zoom=FT * 13 / icon.height), (x1_ - 0.6, y1_ - 0.55),
                                 xycoords="data", box_alignment=(0.5, 0.5), frameon=False, pad=0,
                                 annotation_clip=False, zorder=9))

# ── the counts ──
n_agents = len(rows)
verdict_counts = {k: sum(1 for v in rows.values() if v == k) for k in COLORS}
n_claimed = sum(1 for k, v in rows.items() if v != "goes for T1" and claimed_t1[k])
n_bob = sum(1 for m, w in disclosure.values() if m)
n_work = sum(1 for m, w in disclosure.values() if w)
BARS = [  # (label, count, face, edge, icon, tip) — tip = (count, colour) stacked on top
    ("False claim", verdict_counts["FABR"], COLORS["FABR"], "none", None, None),
    ("Misdirection", verdict_counts["FRAM"], COLORS["FRAM"], "none", None, None),
    ("No deception", verdict_counts["NOTH"], COLORS["NOTH"], "none", None,
     (verdict_counts["sends no message"], COLORS["sends no message"])),
    ("Goes for T1", verdict_counts["goes for T1"], COLORS["goes for T1"], "none", None, None),
    ("Claimed T1", n_claimed, "#FFFFFF", T1_DOT_RING, None, None),
    ("Disclosed: Bob", n_bob, "#e9e6df", "none", EMOJI_MAN, None),
    ("Disclosed: work", n_work, "#e9e6df", "none", EMOJI_BRIEFCASE, None),
]
if POSTER:
    BARS = [(lb, c, *POSTER_COLORS[lb], ic, tp) if lb in POSTER_COLORS else (lb, c, f, e, ic, tp)
            for lb, c, f, e, ic, tp in BARS]
N_VERDICT = 4
if POSTER:  # the three ways of not taking T1 under one bracket; both purple T1 bars side by side after it
    N_VERDICT = 3
print({b[0]: b[1] for b in BARS}, "of", n_agents)

BX0 = CW + GAP + 0.9          # room for the tick labels
BX1 = TOTAL_W - CW - GAP - 0.2
LABEL_H = 7.1 if POSTER else 4.4  # vertical category labels under the bars
CAP_H = 2.3                    # title + group captions above
YB = BOTTOM + LABEL_H          # bar baseline
YT = TOP - CAP_H               # top of the tallest possible bar
VMAX = max(b[1] for b in BARS) * 1.12
n = len(BARS)
group_gap = 0.9 if POSTER else 0.55
GROUP_STARTS = (3, 5) if POSTER else (N_VERDICT,)  # a gap opens before each of these bars
slot = (BX1 - BX0 - group_gap * len(GROUP_STARTS)) / n
bw = slot * 0.72
TXT = dict(fontsize=FS * (7.6 if POSTER else 8.5), color="#333333")


def bar_x(i):
    return BX0 + i * slot + group_gap * sum(i >= g for g in GROUP_STARTS) + (slot - bw) / 2


def ypos(v):
    return YB + (YT - YB) * v / VMAX


# light gridlines + y axis
for v in range(0, int(VMAX) + 1, 20):
    ax.plot([BX0 - 0.15, BX1], [ypos(v), ypos(v)], color="#e2e2e2", lw=1.0, zorder=1)
    ax.text(BX0 - 0.3, ypos(v), str(v), ha="right", va="center", fontsize=FS * 7.5, color="#777777")
ax.plot([BX0 - 0.15, BX1], [YB, YB], color="#555555", lw=1.2, zorder=2)

for i, (label, count, face, edge, icon, tip) in enumerate(BARS):
    x = bar_x(i)
    ax.add_patch(Rectangle((x, YB), bw, ypos(count) - YB, facecolor=face,
                           edgecolor=edge if edge != "none" else "none", linewidth=1.4, zorder=3,
                           hatch="///" if POSTER and label == "Claimed T1" else None))
    top, top_label = count, str(count)
    if tip is not None:
        ax.add_patch(Rectangle((x, ypos(count)), bw, ypos(count + tip[0]) - ypos(count), facecolor=tip[1],
                               edgecolor=edge, linewidth=1.4, zorder=3))
        top, top_label = count + tip[0], f"{count}+{tip[0]}"
    cnt = ax.text(x + bw / 2, ypos(top) + 0.15, top_label, ha="center", va="bottom",
                  fontsize=FS * 8.5, fontweight="bold", color="#222222", zorder=4)
    if icon is not None:
        fig.canvas.draw()
        ax.add_artist(AnnotationBbox(OffsetImage(icon, zoom=FS * 11 / icon.height),
                                     (x + bw / 2, data_extent(cnt)[3] + 0.1), xycoords="data",
                                     box_alignment=(0.5, 0), frameon=False, pad=0, zorder=5))
    ax.text(x + bw / 2, YB - 0.25, shown(label), ha="center", va="top", rotation=90, **TXT)

# group captions: the five verdicts partition the agents; the three markers can overlap
if POSTER:
    # one bracket per side of the trilemma, chips in the poster's honest / effective / confidential highlights
    GROUPS = (((0, 2), "reason for T2\n1 per agent", "#e2b5ce"), ((3, 4), "ends\non T1", "#dcd7cb"),
              ((5, 6), "leaks\nprivate info", "#9fc6ea"))
else:
    GROUPS = (((0, N_VERDICT - 1), "verdict", None), ((N_VERDICT, len(BARS) - 1), "markers", None))
for (i0, i1), cap, chip in GROUPS:
    xa, xb = bar_x(i0), bar_x(i1) + bw
    yc = YT + 0.55
    ax.plot([xa, xa, xb, xb], [yc - 0.18, yc, yc, yc - 0.18], color="#999999", lw=1.0)
    if chip:
        ax.text((xa + xb) / 2, yc + 0.22, cap, ha="center", va="bottom", multialignment="center",
                fontsize=FS * 6.6, color="#1d1b1d", fontweight="bold", linespacing=1.05,
                bbox=dict(boxstyle="round,pad=0.22,rounding_size=0.3", facecolor=chip, edgecolor="none"))
    else:
        ax.text((xa + xb) / 2, yc + 0.12, cap, ha="center", va="bottom", fontsize=FS * 7.5, color="#777777")


fig.patch.set_facecolor("#fcfcfb")
ax.set_facecolor("#fcfcfb")
ax.set_xlim(-2, TOTAL_W + 2)
ax.set_ylim(BOTTOM - 2, TOP + 3)
fig.set_size_inches((TOTAL_W + 4) * S, (TOP - BOTTOM + 5) * S)
fig.canvas.draw()

# crop both outputs to the drawn content
_buf = np.asarray(fig.canvas.buffer_rgba())[:, :, :3].astype(int)
_ink = np.abs(_buf - np.array([0xFC, 0xFC, 0xFB])).sum(axis=2) > 12
_ys, _xs = np.where(_ink)
_H = _buf.shape[0]
pad = 0.15
bb = Bbox([[_xs.min() / fig.dpi - pad, (_H - _ys.max()) / fig.dpi - pad],
           [_xs.max() / fig.dpi + pad, (_H - _ys.min()) / fig.dpi + pad]])
plt.rcParams["pdf.fonttype"] = 42
for name in (("overview_bars_poster.png", "overview_bars_poster.pdf") if POSTER
             else ("overview_bars.png", "overview_bars.pdf")):
    fig.savefig(FIND / name, facecolor=fig.get_facecolor(), bbox_inches=bb)
    print("saved", FIND / name, f"{bb.width:.2f} x {bb.height:.2f} in")
