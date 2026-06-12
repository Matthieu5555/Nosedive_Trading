"""Render a Markdown doc (with images) to a clean PDF via mistune + headless Chrome/Edge.

No extra dependency: mistune ships with the ``notebooks`` group (jupyter/nbconvert), and a
Chromium-based browser (Chrome or Edge) is used for printing. Images referenced by the
Markdown are inlined as base64 so the intermediate HTML is self-contained. Print CSS keeps
figures whole, caps their height to keep pages dense, and avoids forced section breaks.

Usage:
    uv run --group notebooks python scripts/export_doc_pdf.py [path/to/doc.md]

Defaults to documentation/vol-surface/vol_surface_pedagogique.md. The PDF is written next to the
Markdown with the same stem. Override the browser with the CHROME_PDF_BIN environment variable
(headless Chrome/Chromium also works on Linux, e.g. CHROME_PDF_BIN=/usr/bin/chromium).
"""

from __future__ import annotations

import base64
import os
import pathlib
import re
import subprocess
import sys
import tempfile

import mistune
from algotrading.core.paths import repo_root

DEFAULT_MD = repo_root() / "documentation" / "vol-surface" / "vol_surface_pedagogique.md"

BROWSER_CANDIDATES = [
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/microsoft-edge",
    r"C:/Program Files/Google/Chrome/Application/chrome.exe",
    r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    r"C:/Program Files/Microsoft/Edge/Application/msedge.exe",
]

CSS = """
@page { size: A4; margin: 14mm 15mm; }
* { box-sizing: border-box; }
body { font-family: 'Segoe UI', 'Inter', system-ui, sans-serif; font-size: 9.5pt;
       line-height: 1.4; color: #1e293b; max-width: 100%; }
h1 { font-size: 18pt; color: #0f172a; margin: 0 0 3pt; }
h2 { font-size: 13.5pt; color: #0f172a; margin: 14pt 0 6pt; padding-bottom: 3pt;
     border-bottom: 2px solid #2563eb; page-break-after: avoid; }
h3 { font-size: 11.5pt; color: #1e3a5f; margin: 11pt 0 4pt; page-break-after: avoid; }
p { margin: 4pt 0; orphans: 2; widows: 2; }
img { max-width: 100%; max-height: 10cm; height: auto; display: block; margin: 5pt auto;
      border: 1px solid #e2e8f0; border-radius: 4px; break-inside: avoid;
      page-break-inside: avoid; }
blockquote { border-left: 4px solid #94a3b8; background: #f8fafc; margin: 8pt 0;
             padding: 6pt 12pt; color: #475569; font-size: 9.8pt; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0; font-size: 9.5pt;
        page-break-inside: avoid; }
th, td { border: 1px solid #cbd5e1; padding: 5pt 8pt; text-align: left; vertical-align: top; }
th { background: #1e293b; color: #fff; }
tr:nth-child(even) td { background: #f1f5f9; }
code { background: #eef2ff; color: #3730a3; padding: 1px 4px; border-radius: 3px;
       font-family: 'Cascadia Code', Consolas, monospace; font-size: 9.5pt; }
hr { border: none; border-top: 1px solid #e2e8f0; margin: 14pt 0; }
strong { color: #0f172a; }
ul, ol { margin: 6pt 0; padding-left: 20pt; }
li { margin: 3pt 0; }
"""


def find_browser() -> str:
    """Return a Chromium-based browser path (env override, then known locations)."""
    override = os.environ.get("CHROME_PDF_BIN")
    if override and pathlib.Path(override).exists():
        return override
    for cand in BROWSER_CANDIDATES:
        if pathlib.Path(cand).exists():
            return cand
    raise SystemExit(
        "No Chrome/Edge/Chromium found. Set CHROME_PDF_BIN to a Chromium-based browser executable."
    )


def render_html(md_path: pathlib.Path) -> str:
    """Render Markdown to a self-contained HTML string (images inlined as base64)."""
    body = mistune.create_markdown(plugins=["table"], escape=False)(
        md_path.read_text(encoding="utf-8")
    )
    if not isinstance(body, str):  # the default html renderer returns str, never tokens
        raise SystemExit(f"mistune returned {type(body).__name__}, not HTML")

    def inline(match: re.Match[str]) -> str:
        src = match.group(1)
        img = (md_path.parent / src).resolve()
        if not img.exists():
            raise SystemExit(f"missing image referenced by {md_path.name}: {img}")
        b64 = base64.b64encode(img.read_bytes()).decode()
        return f'src="data:image/png;base64,{b64}"'

    body = re.sub(r'src="([^"]+\.png)"', inline, body)
    return (
        "<!DOCTYPE html><html lang='fr'><head><meta charset='utf-8'>"
        f"<style>{CSS}</style></head><body>{body}</body></html>"
    )


def main() -> None:
    md_path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_MD
    if not md_path.exists():
        raise SystemExit(f"Markdown not found: {md_path}")
    pdf_path = md_path.with_suffix(".pdf").resolve()
    browser = find_browser()

    html = render_html(md_path)
    with tempfile.NamedTemporaryFile("w", suffix=".html", encoding="utf-8", delete=False) as fh:
        fh.write(html)
        tmp = pathlib.Path(fh.name)

    try:
        cmd = [
            browser, "--headless=new", "--disable-gpu", "--no-sandbox",
            "--no-pdf-header-footer", "--virtual-time-budget=15000",
            f"--print-to-pdf={pdf_path}", tmp.as_uri(),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
    finally:
        tmp.unlink(missing_ok=True)

    if not pdf_path.exists():
        sys.stderr.write(result.stderr[-2000:] + "\n")
        raise SystemExit("PDF not produced")
    print(f"PDF: {pdf_path}  ({pdf_path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
