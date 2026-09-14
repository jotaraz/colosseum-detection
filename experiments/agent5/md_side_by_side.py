"""Render one or two Markdown files as HTML — two panes side by side when given two.

    .venv/bin/python -m experiments.agent5.md_side_by_side \
        --left outputs/njv2_v2_strict_fabrications_replicates.md --left-title "v2" \
        --right outputs/njv2_v3_strict_fabrications_replicates.md --right-title "v3" \
        --out outputs/njv2_replicates_v2_vs_v3.html

No dependencies: a small converter for the subset these reports use — headings, tables,
blockquotes, bold/italic/code, rules, lists — plus the `<details>` blocks, which are HTML
already and pass through untouched.
"""

from __future__ import annotations

import argparse
import html
import re
from pathlib import Path
from typing import List, Optional

_INLINE = [
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*\*([^*]+)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"(?<![*\w])\*([^*\n]+)\*(?!\*)"), r"<em>\1</em>"),
    (re.compile(r"(?<!_)_([^_\n]+)_(?!_)"), r"<em>\1</em>"),
]


def inline(text: str) -> str:
    out = html.escape(text, quote=False)
    for pat, repl in _INLINE:
        out = pat.sub(repl, out)
    return out


def _table(rows: List[str]) -> str:
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    body = [c for c in cells[2:]] if len(cells) > 1 and set(cells[1][0]) <= set("-: ") else cells[1:]
    head = "".join(f"<th>{inline(c)}</th>" for c in cells[0])
    trs = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>" for row in body)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{trs}</tbody></table>"


def convert(md: str) -> str:
    out: List[str] = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("<"):                      # raw HTML (details/summary)
            out.append(line)
            i += 1
        elif stripped.startswith("|"):                    # table
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            out.append(_table(block))
        elif stripped.startswith("> "):                   # blockquote
            block = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                block.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append("<blockquote>" + "<br>".join(inline(b) for b in block) + "</blockquote>")
        elif re.match(r"^#{1,6} ", stripped):
            n = len(stripped) - len(stripped.lstrip("#"))
            out.append(f"<h{n}>{inline(stripped[n:].strip())}</h{n}>")
            i += 1
        elif stripped in ("---", "***", "___"):
            out.append("<hr>")
            i += 1
        elif stripped.startswith(("- ", "* ")):
            block = []
            while i < len(lines) and lines[i].strip().startswith(("- ", "* ")):
                block.append(lines[i].strip()[2:])
                i += 1
            out.append("<ul>" + "".join(f"<li>{inline(b)}</li>" for b in block) + "</ul>")
        elif not stripped:
            i += 1
        else:
            block = []
            while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith(
                    ("|", ">", "#", "-", "*", "<")):
                block.append(lines[i].strip())
                i += 1
            if block:
                out.append("<p>" + inline(" ".join(block)) + "</p>")
            else:
                out.append(inline(stripped))
                i += 1
    return "\n".join(out)


CSS = """
:root { color-scheme: light dark; --line: #d0d7de; --muted: #57606a; --bg2: #f6f8fa; }
@media (prefers-color-scheme: dark) {
  :root { --line: #30363d; --muted: #8b949e; --bg2: #161b22; }
  body { background: #0d1117; color: #e6edf3; }
}
* { box-sizing: border-box; }
body { margin: 0; font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
header { position: sticky; top: 0; z-index: 5; display: flex; gap: 1rem; align-items: baseline;
         padding: .6rem 1rem; border-bottom: 1px solid var(--line); background: var(--bg2); }
header h1 { font-size: 15px; margin: 0; } header span { color: var(--muted); font-size: 13px; }
.wrap { display: grid; grid-template-columns: 1fr 1fr; gap: 0; }
.pane { padding: 0 1rem 4rem; overflow-x: auto; min-width: 0; }
.pane + .pane { border-left: 1px solid var(--line); }
.pane > h2.pane-title { position: sticky; top: 0; margin: 0 -1rem 1rem; padding: .5rem 1rem;
  background: var(--bg2); border-bottom: 1px solid var(--line); font-size: 14px; }
h1, h2, h3 { line-height: 1.25; } h2 { font-size: 15px; margin-top: 1.6rem; }
table { border-collapse: collapse; margin: .6rem 0; font-size: 12.5px; width: 100%; }
th, td { border: 1px solid var(--line); padding: 3px 7px; text-align: left; vertical-align: top; }
th { background: var(--bg2); white-space: nowrap; }
blockquote { margin: .5rem 0; padding: .3rem .8rem; border-left: 3px solid var(--line);
             color: var(--muted); }
code { background: var(--bg2); padding: 0 3px; border-radius: 3px; font-size: 12.5px; }
details { margin: .4rem 0; } summary { cursor: pointer; padding: 2px 0; }
hr { border: 0; border-top: 1px solid var(--line); margin: 1.5rem 0; }
@media (max-width: 900px) { .wrap { grid-template-columns: 1fr; }
  .pane + .pane { border-left: 0; border-top: 2px solid var(--line); } }
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--left", required=True)
    ap.add_argument("--right", default=None)
    ap.add_argument("--left-title", default="left")
    ap.add_argument("--right-title", default="right")
    ap.add_argument("--title", default="njv2 replicate comparison")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    def pane(path: Optional[str], title: str) -> str:
        if not path:
            return ""
        return (f'<div class="pane"><h2 class="pane-title">{html.escape(title)}</h2>'
                + convert(Path(path).read_text(encoding="utf-8")) + "</div>")

    body = pane(args.left, args.left_title) + pane(args.right, args.right_title)
    Path(args.out).write_text(
        f"<!doctype html><meta charset='utf-8'><title>{html.escape(args.title)}</title>"
        f"<style>{CSS}</style>"
        f"<header><h1>{html.escape(args.title)}</h1>"
        f"<span>generated from the Markdown reports · toggle each item to compare</span></header>"
        f"<div class='wrap'>{body}</div>", encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
