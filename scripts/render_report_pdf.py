"""Render a markdown report to PDF with its figures embedded.

Markdown → styled HTML → PDF through the Playwright Chromium already used for
UI checks, so there is no pandoc/LaTeX dependency. Images are inlined as base64
data URIs, which keeps the PDF self-contained and sidesteps Chromium's
file:// image restrictions.

Run:
    uv run --with playwright --with markdown python scripts/render_report_pdf.py \
        docs/report_somics_datasets.md
"""

import argparse
import base64
import mimetypes
import re
from pathlib import Path

import markdown

CSS = """
@page { size: A4; margin: 16mm 14mm; }
body { font-family: -apple-system, "Helvetica Neue", Arial, sans-serif;
       color: #1a1a1a; line-height: 1.55; font-size: 10.5pt; max-width: 100%; }
h1 { font-size: 21pt; margin: 0 0 .2em; letter-spacing: -.01em; }
h2 { font-size: 14pt; margin: 1.5em 0 .5em; border-bottom: 1px solid #e1e0d9;
     padding-bottom: .25em; page-break-after: avoid; }
h1 + p { font-size: 11pt; color: #52514e; }
em { color: #6b6a66; }
img { max-width: 100%; margin: .8em 0; page-break-inside: avoid; }
table { border-collapse: collapse; margin: .8em 0; font-size: 10pt; }
th, td { border-bottom: 1px solid #e1e0d9; padding: .35em .8em .35em 0; text-align: left; }
th { color: #52514e; font-weight: 600; }
code { background: #f4f3ee; padding: .1em .3em; border-radius: 3px; font-size: 9.5pt; }
hr { border: 0; border-top: 1px solid #e1e0d9; margin: 1.6em 0; }
ul, ol { padding-left: 1.2em; }
li { margin: .25em 0; }
"""


def inline_images(html, base):
    def sub(m):
        src = m.group(1)
        p = (base / src).resolve()
        if not p.is_file():
            return m.group(0)
        mime = mimetypes.guess_type(p.name)[0] or "image/png"
        b64 = base64.b64encode(p.read_bytes()).decode()
        return f'src="data:{mime};base64,{b64}"'

    return re.sub(r'src="([^"]+)"', sub, html)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path)
    ap.add_argument("-o", "--output", type=Path)
    args = ap.parse_args()
    out = args.output or args.source.with_suffix(".pdf")

    body = markdown.markdown(args.source.read_text(), extensions=["tables", "attr_list"])
    body = inline_images(body, args.source.parent)
    html = f"<!doctype html><meta charset='utf-8'><style>{CSS}</style>{body}"

    tmp = args.source.parent / (args.source.stem + ".render.html")
    tmp.write_text(html)
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            page.goto(tmp.resolve().as_uri(), wait_until="networkidle")
            page.pdf(
                path=str(out),
                format="A4",
                print_background=True,
                margin={"top": "16mm", "bottom": "16mm", "left": "14mm", "right": "14mm"},
            )
            browser.close()
    finally:
        tmp.unlink(missing_ok=True)
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
