"""Build the whole GitHub Pages site: one subfolder per tenant, a landing page, and redirects for old links.

    python scripts/build_site.py --out site

Every tenant with a committed findings export (reports/<tenant>/findings.json) gets its latest report at
<out>/<tenant>/ and its archive at <out>/<tenant>/archive/. Renders only committed files: no store, no Google calls.
The site once served the first tenant's report at the root; LEGACY_TENANT keeps those links working
(/#<anchor> and /archive/<date>/ redirect into its subfolder).
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.paths import published_tenants, tenant_paths  # noqa: E402
from core.tenant import load_tenant  # noqa: E402
from scripts import build_archive, build_report  # noqa: E402

LEGACY_TENANT = "aspose.app"  # its report was the site root until 2026-10-20

HEAD = """<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
"""

LANDING = HEAD + """<title>Site health reports</title>
<style>
:root { --bg: #f6f7f9; --surface: #fff; --fg: #18202b; --muted: #5b6676; --line: #dde2e9; --accent: #1f5f8b }
@media (prefers-color-scheme: dark) { :root { --bg: #11161d; --surface: #182029; --fg: #e4e9f0; --muted: #98a3b3;
  --line: #2a3542; --accent: #6fb0dc; color-scheme: dark } }
* { box-sizing: border-box }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif }
main { max-width: 760px; margin: 0 auto; padding: 32px 16px 64px; display: grid; gap: 20px }
h1 { margin: 0; font-size: 28px } p { margin: 0; color: var(--muted) }
a { color: var(--accent) }
ol { margin: 0; padding: 0; list-style: none; display: grid; gap: 10px }
li { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px;
  display: grid; gap: 4px }
li b { font-size: 18px } li span { color: var(--muted); font-size: 14px; overflow-wrap: anywhere }
</style>
<script>
// Old links pointed at the first tenant's report on this page (e.g. /#findings): send them there.
if (location.hash.length > 1) location.replace("__LEGACY__/" + location.hash);
</script>
<main>
  <h1>Site health reports</h1>
  <p>One report per property. Read-only audits: nothing on any site is changed.</p>
  <ol>
__ITEMS__
  </ol>
</main>
</html>
"""


def redirect_page(target: str) -> str:
    """A page that forwards to `target` (relative), keeping the #anchor."""
    t = html.escape(target, quote=True)
    return (HEAD + f"<title>Moved</title>\n<script>location.replace({json.dumps(target)} + location.hash);</script>\n"
            f'<meta http-equiv="refresh" content="0; url={t}">\n'
            f'<p>This page moved to <a href="{t}">{t}</a>.</p>\n</html>\n')


def tenant_item(tenant: str) -> str:
    rows = json.loads(tenant_paths(tenant).findings_export.read_text(encoding="utf-8"))
    open_n = sum(1 for f in rows if f["status"] in ("open", "unverified"))
    hosts = load_tenant(tenant).host_names
    name = html.escape(tenant)
    host_text = ", ".join(hosts) if len(hosts) <= 4 else f"{len(hosts)} hosts"
    return (f'    <li><a href="{name}/"><b>{name}</b></a><span>{html.escape(host_text)} · {open_n:,} open findings · '
            f'<a href="{name}/archive/">previous versions</a></span></li>')


def build(out: Path, tenants: list[str]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for t in tenants:
        paths = tenant_paths(t)
        rc = build_report.main(["--tenant", t, "--findings", str(paths.findings_export), "--standalone",
                                "--out", str(out / t / "index.html")])
        if rc:
            raise SystemExit(f"Report for {t} failed")
        n = build_archive.build(out / t / "archive", paths.archive, t)
        print(f"Archive: {n} snapshots for {t}")
    legacy = LEGACY_TENANT if LEGACY_TENANT in tenants else (tenants[0] if tenants else "")
    items = "\n".join(tenant_item(t) for t in tenants)
    (out / "index.html").write_text(LANDING.replace("__LEGACY__", legacy).replace("__ITEMS__", items),
                                    encoding="utf-8")
    if legacy:  # /archive/ and /archive/<date>/ from before the subfolders
        (out / "archive").mkdir(exist_ok=True)
        (out / "archive" / "index.html").write_text(redirect_page(f"../{legacy}/archive/"), encoding="utf-8")
        for e in build_archive.entries(tenant_paths(legacy).archive):
            slug = Path(e["file"]).stem
            (out / "archive" / slug).mkdir(exist_ok=True)
            (out / "archive" / slug / "index.html").write_text(
                redirect_page(f"../../{legacy}/archive/{slug}/"), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="site", help="Pages output folder")
    ap.add_argument("--tenants", help="comma-separated tenants "
                    "(default: every tenant with reports/<tenant>/findings.json)")
    args = ap.parse_args(argv)
    tenants = args.tenants.split(",") if args.tenants else published_tenants()
    if not tenants:
        print("No reports/<tenant>/findings.json found.", file=sys.stderr)
        return 1
    build(Path(args.out), tenants)
    print(f"Site: {', '.join(tenants)} in {args.out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
