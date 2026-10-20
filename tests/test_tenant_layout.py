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
