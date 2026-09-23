"""Split findings/full_image_stacked.png into example_convo.png and full_image_2col.png.

Cuts at the widest blank band between the conversation and the overview, then pads both
halves with an even margin. Run after full_image_stacked.py.
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops

FINDINGS = Path(__file__).resolve().parents[1] / "findings"
BG = (0xFC, 0xFC, 0xFB)
MARGIN = 50

im = Image.open(FINDINGS / "full_image_stacked.png").convert("RGB")
a = np.asarray(im).astype(int)
blank = (np.abs(a - np.array(BG)).sum(axis=2) < 12).all(axis=1)
h = len(blank)
best, start = (0, 0, 0), None
for y in range(int(h * 0.1), int(h * 0.6)):
    if blank[y] and start is None:
        start = y
    if (not blank[y] or y == int(h * 0.6) - 1) and start is not None:
        if y - start > best[0]:
            best = (y - start, start, y)
        start = None
_, g0, g1 = best


def save(img, path):
    x0, y0, x1, y1 = ImageChops.difference(img, Image.new("RGB", img.size, BG)).getbbox()
    content = img.crop((x0, y0, x1, y1))
    out = Image.new("RGB", (content.width + 2 * MARGIN, content.height + 2 * MARGIN), BG)
    out.paste(content, (MARGIN, MARGIN))
    out.save(path)
    print("saved", path, out.size)


save(im.crop((0, 0, im.width, g0)), FINDINGS / "example_convo.png")
save(im.crop((0, g1, im.width, im.height)), FINDINGS / "full_image_2col.png")
