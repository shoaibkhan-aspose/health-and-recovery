"""Logo and favicon for every page the site builds. The source files live in docs/brand/; pages inline them (data URI
favicon, inline SVG logo) so host pages, archive snapshots and local builds need no extra files or paths."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

BRAND_DIR = Path(__file__).resolve().parent.parent / "docs" / "brand"


def _svg(name: str) -> str:
    return " ".join((BRAND_DIR / name).read_text(encoding="utf-8").split())


FAVICON_LINK = f'<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,{quote(_svg("favicon.svg"))}">'


def logo(size: int = 28) -> str:
    """The logo mark as inline SVG, decorative (the page title names the app)."""
    svg = _svg("logo-mark.svg").replace(' role="img" aria-label="Site Health &amp; Recovery"', "")
    return svg.replace("<svg ", f'<svg class="logo" width="{size}" height="{size}" aria-hidden="true" ', 1)
