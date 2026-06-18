"""Mount the marimo apps behind one FastAPI shell with a persistent nav bar.

A simple stand-in "frontend": every marimo app in this folder becomes a route.
Each notebook is embedded in an iframe under a shared top nav, so you can always
move between apps (and back home) without losing the chrome. Each app keeps its
live selectors and reads the real banked store through ``AppContext`` exactly as
it does under ``marimo run``.

    uv run --group notebooks python notebooks/serve.py            # port 8200
    uv run --group notebooks python notebooks/serve.py --port 9001

Then open http://127.0.0.1:8200/ . The marimo apps themselves are mounted under
``/app/<name>/``; the human-facing routes (``/vol`` etc.) wrap them in the nav.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import marimo
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

NOTEBOOKS = Path(__file__).parent

# (slug, file, nav label, one-line what-it-shows)
# The dashboard is the landing view; the seven single-purpose apps are the
# component views it draws on (see README.md).
APPS = [
    ("dashboard", "risk_dashboard.py", "PM dashboard", "What's my risk, what moved it, where can we blow up"),
    ("vol", "vol_surface.py", "Vol surface", "3D SVI nappe + heatmap + ATM term structure"),
    ("greeks", "greeks.py", "Greeks", "Dollar greeks by maturity"),
    ("market", "market.py", "Market", "Price history + index constituents"),
    ("coverage", "coverage.py", "Coverage", "Captured chain map + QC floor pass/fail"),
    ("basket", "basket_risk.py", "Basket risk", "Basket dollar greeks / leg breakdown"),
    ("scenarios", "scenarios.py", "Scenarios", "Spot x vol stress P&L heatmap"),
    ("attribution", "attribution.py", "P&L attribution", "Greek-by-greek P&L waterfall"),
]

_SHELL = """<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>
  html, body {{ margin: 0; height: 100%; font: 14px system-ui, sans-serif; }}
  body {{ display: flex; flex-direction: column; }}
  header {{ display: flex; align-items: center; gap: .25rem; flex-wrap: wrap;
            padding: .5rem .9rem; border-bottom: 1px solid #e5e5e5;
            background: #fafafa; }}
  header .brand {{ font-weight: 700; margin-right: .8rem; }}
  header a {{ text-decoration: none; color: #444; padding: .3rem .6rem;
             border-radius: 6px; }}
  header a:hover {{ background: #ececec; }}
  header a.active {{ background: #2563eb; color: #fff; }}
  iframe {{ flex: 1; border: 0; width: 100%; }}
</style></head><body>
<header>
  <a class="brand" href="/">marimo gallery</a>
  {nav}
</header>
{body}
</body></html>"""


def _nav(active: str) -> str:
    return "".join(
        f'<a class="{"active" if slug == active else ""}" '
        f'href="/{slug}" title="{desc}">{label}</a>'
        for slug, _f, label, desc in APPS
    )


def build_app() -> FastAPI:
    server = marimo.create_asgi_app()
    for slug, filename, _label, _desc in APPS:
        path = NOTEBOOKS / filename
        if not path.exists():
            raise FileNotFoundError(f"notebook missing: {path}")
        server = server.with_app(path=f"/app/{slug}", root=str(path))

    app = FastAPI(title="marimo gallery")

    @app.get("/", response_class=HTMLResponse)
    def landing() -> str:
        cards = "".join(
            f'<li style="margin:.6rem 0;padding:.8rem 1rem;border:1px solid #e5e5e5;'
            f'border-radius:8px;list-style:none">'
            f'<a style="font-weight:600;color:#2563eb;text-decoration:none" '
            f'href="/{slug}">{label}</a>'
            f'<span style="display:block;color:#666;margin-top:.15rem">{desc}</span></li>'
            for slug, _f, label, desc in APPS
        )
        body = (
            '<div style="max-width:760px;margin:2rem auto;padding:0 1.5rem">'
            '<p style="color:#666">One FastAPI shell, one marimo app per route. '
            'Reads the real offline store. Use the bar above to move between apps.</p>'
            f'<ul style="padding:0">{cards}</ul></div>'
        )
        return _SHELL.format(title="marimo gallery", nav=_nav(""), body=body)

    def _make_page(slug: str, label: str):
        def page() -> str:
            body = f'<iframe src="/app/{slug}/" title="{label}"></iframe>'
            return _SHELL.format(title=f"{label} - marimo", nav=_nav(slug), body=body)

        return page

    for slug, _filename, label, _desc in APPS:
        app.add_api_route(
            f"/{slug}", _make_page(slug, label), response_class=HTMLResponse
        )

    app.mount("/", server.build())
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8200)
    args = parser.parse_args()
    uvicorn.run(build_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
