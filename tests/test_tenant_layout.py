"""Per-tenant layout: data/<tenant>/, reports/<tenant>/, and the Pages site with one subfolder per tenant."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.paths import published_tenants, tenant_paths  # noqa: E402
from core.tenant import load_tenant  # noqa: E402


def test_tenant_paths(tmp_path):
    p = tenant_paths("example.org", tmp_path / "data", tmp_path / "reports")
    assert p.findings_store == tmp_path / "data" / "example.org" / "findings.sqlite"
    assert p.findings_export == tmp_path / "reports" / "example.org" / "findings.json"
    assert p.archive == tmp_path / "reports" / "example.org" / "archive"
    for bad in ("", "../x", "a/b", ".hidden"):
        with pytest.raises(ValueError):
            tenant_paths(bad)


def test_published_tenants(tmp_path):
    for t in ("b.org", "a.org"):
        (tmp_path / t).mkdir()
        (tmp_path / t / "findings.json").write_text("[]")
    (tmp_path / "no-report.org").mkdir()
    assert published_tenants(tmp_path) == ["a.org", "b.org"]


def test_property_must_match_file_name(tmp_path):
    (tmp_path / "x.org.yaml").write_text("property: y.org\nhosts: []\n")
    with pytest.raises(ValueError, match="must match"):
        load_tenant("x.org", tmp_path)


def test_redirect_keeps_anchor():
    from scripts.build_site import redirect_page

    page = redirect_page("../aspose.app/archive/")
    assert 'location.replace("../aspose.app/archive/" + location.hash)' in page
    assert page.index("<script>") < page.index('http-equiv="refresh"')  # the script runs first, keeping #anchor
    assert "noindex, nofollow" in page


def test_report_without_family_hosts(tmp_path):
    """A tenant whose traffic summary has no sectioned hosts gets no family cards (and no crash)."""
    from scripts.build_report import add_families, add_views

    summary = tmp_path / "traffic-summary.json"
    summary.write_text(json.dumps({"windows": {"current": ["2026-01-01", "2026-01-28"]}, "hosts": {}}))
    report = {"run_id": 1, "findings": [], "sections": {}}
    add_families(report, summary)
    assert "_families" not in report
    add_views(report, [], tmp_path / "health-history.json")
    assert "_run_changes" in report


def test_host_cards(tmp_path):
    """Hosts without family cards get a health card scored like a family, with their search traffic."""
    from scripts.build_report import add_hosts

    summary = tmp_path / "traffic-summary.json"
    summary.write_text(json.dumps({"windows": {}, "hosts": {}, "host_totals": {
        "blog.example.org": {"source": "gsc", "current": {"clicks": 5, "impressions": 50, "pages": 3}}}}))
    finding = {"id": "onpage-0001", "status": "open", "type": "incorrect", "check": "title_missing",
               "subdomain": "blog.example.org", "template": "page", "title": "No title",
               "evidence": {"urls": ["https://blog.example.org/"], "sample": {"post": [1, 4]}}}
    report = {"hosts": {"blog.example.org": {"urls": 40}, "www.example.org": {"urls": 1}}, "findings": [finding],
              "_families": {"hosts": {"www.example.org": []}}}
    add_hosts(report, summary)
    card, www = report["_hosts"]  # www has a family host entry but no cards: plain host score, no rollup
    assert card["host"] == "blog.example.org" and card["urls"] == 40 and "rollup" not in www
    assert card["health"] == 95 and card["grade"] == "A"  # incorrect 20 x share 0.25
    assert card["traffic"]["current"]["clicks"] == 5


def test_fix_guide_page(tmp_path, monkeypatch):
    """One shared guide page with an anchor per check; reports link to it instead of embedding the table."""
    from core.fix_guide import GUIDE
    from scripts import build_site
    from scripts.build_report import build

    monkeypatch.setattr(build_site, "open_by_check", lambda tenants: {"title_missing": {"a.org": 2}})
    page = build_site.guide_page(["a.org"])
    assert page.count('<tr id="g-') == len(GUIDE)
    assert '<a href="../a.org/">a.org</a> 2' in page
    report = {"tenant": "a.org", "findings": [{"_guide": "title_missing"}],
              "_guide": {"areas": [], "rows": {"title_missing": {}, "h1_missing": {}}}}
    html = build(report, [], None, guide_url="../fix-guide/")
    assert '"_guide_url": "../fix-guide/"' in html and "h1_missing" not in html  # only rows the report uses


def test_family_host_rollup():
    """A host with families: click-weighted family health minus host-wide findings; the lowest family is named."""
    from scripts.build_report import family_rollup

    cards = [{"family": "a", "health": 90, "grade": "A", "traffic": {"current": {"clicks": 300}}},
             {"family": "b", "health": 50, "grade": "D", "traffic": {"current": {"clicks": 100}}}]
    host_wide = {"id": "crawl-0001", "status": "open", "type": "broken", "check": "robots_unreachable",
                 "subdomain": "p.example.org", "template": "host", "title": "robots.txt cannot be reached",
                 "evidence": {"urls": ["https://p.example.org/robots.txt"]}}
    r = family_rollup("p.example.org", cards, [host_wide], 1000)
    assert r["rollup"]["weighted"] == 80 and r["rollup"]["host_wide"] == 40  # (90*300 + 50*100) / 400; broken x 1.0
    assert r["health"] == 40 and r["grade"] == "D" and r["rollup"]["lowest"]["family"] == "b"
