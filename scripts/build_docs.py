"""Renders the Mermaid diagrams to SVG + PNG and the Markdown docs to PDF, using headless Chrome.

Why this approach: no extra tooling to install (pandoc, mermaid-cli, LaTeX); Chrome renders Mermaid
exactly like GitHub does and prints clean PDFs. Diagram sources (.mmd) stay the single source of truth.

Usage:
    python scripts/build_docs.py                      # diagrams + docs/technical_specification.pdf
    python scripts/build_docs.py docs/other.md ...    # also/instead build other Markdown files
Needs: Google Chrome (override the path with CHROME=...), internet access (Mermaid loads from a CDN).
"""

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
DIAGRAMS = DOCS / "diagrams"
PDF_DIR = DOCS / "pdf"
CHROME = os.getenv("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
MERMAID_JS = "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js"
TMP = Path(tempfile.mkdtemp(prefix="build_docs_"))


def chrome(*args: str) -> str:
    cmd = [CHROME, "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           "--hide-scrollbars", f"--user-data-dir={TMP / 'profile'}", *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=180).stdout


# ---------------------------------------------------------------------------
# Diagrams
# ---------------------------------------------------------------------------

def render_svg(mmd: Path) -> str:
    # htmlLabels=false makes Mermaid draw labels as real SVG text (no <foreignObject>),
    # so the .svg also displays correctly outside a browser (viewers, PDF, slides).
    page = TMP / f"{mmd.stem}.html"
    page.write_text(f"""<!doctype html><html><body><div id="out"></div><pre id="xml"></pre>
<script src="{MERMAID_JS}"></script>
<script>
mermaid.initialize({{startOnLoad: false, theme: "base",
  themeVariables: {{fontFamily: "Helvetica, Arial, sans-serif", fontSize: "15px",
                    lineColor: "#555", primaryBorderColor: "#777", clusterBkg: "#fafafa",
                    edgeLabelBackground: "#ffffff"}},
  flowchart: {{htmlLabels: false, curve: "basis", padding: 12, wrappingWidth: 420}}}});
mermaid.render("d", {json.dumps(mmd.read_text(encoding="utf-8"))})
  .then(r => {{
    document.getElementById("out").innerHTML = r.svg;
    // XMLSerializer gives valid standalone SVG; base64 keeps it intact through --dump-dom.
    const xml = new XMLSerializer().serializeToString(document.querySelector("#out svg"));
    document.getElementById("xml").textContent = btoa(unescape(encodeURIComponent(xml)));
  }})
  .catch(e => document.getElementById("out").textContent = "MERMAID_ERROR " + e);
</script></body></html>""", encoding="utf-8")
    dom = chrome("--virtual-time-budget=20000", "--dump-dom", page.as_uri())
    # Look inside the output <div> only: the page's own <script> also contains the marker text.
    if '<div id="out">MERMAID_ERROR' in dom:
        raise SystemExit(f"{mmd.name}: " + dom.split('<div id="out">MERMAID_ERROR', 1)[1].split("<", 1)[0])
    match = re.search(r'<pre id="xml">([A-Za-z0-9+/=]+)</pre>', dom)
    if not match:
        raise SystemExit(f"{mmd.name}: no SVG produced (is the Mermaid CDN reachable?)")
    svg = base64.b64decode(match.group(1)).decode("utf-8")
    ET.fromstring(svg)  # fail loudly if the result isn't valid standalone XML
    return svg


def svg_size(svg: str) -> tuple[int, int]:
    _, _, w, h = (float(v) for v in re.search(r'viewBox="([^"]+)"', svg).group(1).split())
    return int(w) + 1, int(h) + 1


def render_png(svg_path: Path, png_path: Path, svg: str) -> None:
    w, h = svg_size(svg)
    pad = 24
    page = TMP / f"{svg_path.stem}_png.html"
    page.write_text(f"""<!doctype html><html><body style="margin:0;background:#fff">
<img src="{svg_path.as_uri()}" style="display:block;margin:{pad}px;width:{w}px;height:{h}px"></body></html>""",
                    encoding="utf-8")
    # 2x device scale -> crisp PNG for slides and README.
    chrome("--force-device-scale-factor=2", f"--window-size={w + 2 * pad},{h + 2 * pad}",
           "--default-background-color=ffffffff", f"--screenshot={png_path}", page.as_uri())


def build_diagrams() -> None:
    for mmd in sorted(DIAGRAMS.glob("*.mmd")):
        svg = render_svg(mmd)
        svg_path = mmd.with_suffix(".svg")
        svg_path.write_text(svg, encoding="utf-8")
        render_png(svg_path, mmd.with_suffix(".png"), svg)
        print(f"diagram  {mmd.relative_to(ROOT)} -> .svg, .png  ({'x'.join(map(str, svg_size(svg)))})")


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

CSS = """
@page { size: A4; margin: 16mm 14mm 18mm 14mm; }
body { font-family: -apple-system, Helvetica, Arial, sans-serif; font-size: 10.5pt; line-height: 1.45; color: #1f2328; }
h1 { font-size: 20pt; border-bottom: 2px solid #1f2328; padding-bottom: 4px; }
h2 { font-size: 14pt; margin-top: 22px; border-bottom: 1px solid #d0d7de; padding-bottom: 3px; break-after: avoid; }
h3 { font-size: 11.5pt; margin-top: 16px; break-after: avoid; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 9.5pt; break-inside: auto; }
th, td { border: 1px solid #d0d7de; padding: 4px 6px; text-align: left; vertical-align: top; }
th { background: #f6f8fa; }
tr { break-inside: avoid; }
code { font-family: Menlo, Consolas, monospace; font-size: 9pt; background: #f6f8fa; padding: 0 3px; border-radius: 3px; }
pre { background: #f6f8fa; padding: 8px 10px; border-radius: 4px; overflow-x: hidden; white-space: pre-wrap; break-inside: avoid; }
pre code { padding: 0; }
img { max-width: 100%; display: block; margin: 8px auto; break-inside: avoid; }
/* Each diagram gets its own page, scaled to fit, so tall figures never leave half-empty pages. */
p:has(> img) { break-before: page; break-after: avoid; margin: 0; }
p:has(> img) img { max-height: 238mm; width: auto; }
blockquote { margin: 8px 0; padding: 4px 12px; border-left: 4px solid #d0d7de; color: #57606a; }
a { color: #0969da; text-decoration: none; }
"""


def build_pdf(md_path: Path) -> None:
    body = markdown.markdown(md_path.read_text(encoding="utf-8"),
                             extensions=["tables", "fenced_code", "sane_lists", "toc"])
    # Use the vector SVG in the PDF (sharp at any zoom); the Markdown links the PNG for portability.
    body = re.sub(r'src="(diagrams/[^"]+)\.png"', r'src="\1.svg"', body)
    title = md_path.stem.replace("_", " ").title()
    html = TMP / f"{md_path.stem}.html"
    html.write_text(f"""<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<base href="{md_path.parent.as_uri()}/"><style>{CSS}</style></head><body>{body}</body></html>""",
                    encoding="utf-8")
    PDF_DIR.mkdir(exist_ok=True)
    out = PDF_DIR / f"{md_path.stem}.pdf"
    chrome("--no-pdf-header-footer", f"--print-to-pdf={out}", html.as_uri())
    if not out.exists() or out.stat().st_size == 0:
        raise SystemExit(f"PDF not produced for {md_path}")
    print(f"pdf      {md_path.relative_to(ROOT)} -> {out.relative_to(ROOT)}  ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    if not Path(CHROME).exists():
        raise SystemExit(f"Chrome not found at {CHROME}; set CHROME=/path/to/chrome")
    build_diagrams()
    targets = [Path(p).resolve() for p in sys.argv[1:]] or [DOCS / "technical_specification.md"]
    for md in targets:
        build_pdf(md)
