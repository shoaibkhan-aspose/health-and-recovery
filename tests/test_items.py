"""Offline tests for the item ledger: fixed / new / removed / still open across link-check runs."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.items import counts, run_items, update  # noqa: E402

HOST = "blog.example.org"


def run(links, partial=None):
    """A link-check run with one broken link per (file, link) pair."""
    return {"hosts": {HOST: {"issues": [{"check": "link_broken", "page": f"https://{HOST}/{f}/", "file": f, "link": lk}
                                        for f, lk in links],
                             "partial": partial or [], "unsupported": {}}}}


def test_sk_example_fixed_share_falls_when_new_issues_arrive(tmp_path):
    """1,000 issues, 200 fixed: 20%. Then 1,000 new: 200 of 2,000, 10% (SK, 2026-10-27)."""
    for i in range(1000):
        (tmp_path / f"p{i}.md").write_text("x")
    repos = {HOST: str(tmp_path)}
    first = [(f"p{i}.md", "/gone/") for i in range(1000)]
    ledger = update({}, "link-audit-run1", "2026-10-22", run_items(run(first)), repos)
    ledger = update(ledger, "link-audit-run2", "2026-10-29", run_items(run(first[200:])), repos)
    c = counts(ledger, HOST)
    assert (c["baseline"], c["fixed"], c["open"], c["fixed_pct"]) == (1000, 200, 800, 20)
    more = first[200:] + [(f"p{i}.md", "/also-gone/") for i in range(1000)]
    ledger = update(ledger, "link-audit-run3", "2026-11-05", run_items(run(more)), repos)
    c = counts(ledger, HOST)
    assert (c["new"], c["fixed"], c["open"], c["fixed_pct"]) == (1000, 200, 1800, 10)
    assert update(ledger, "link-audit-run3", "2026-11-05", {}, repos) is ledger  # a run counts once


def test_deleted_post_is_removed_not_fixed_and_items_can_come_back(tmp_path):
    (tmp_path / "kept.md").write_text("x")
    repos = {HOST: str(tmp_path)}
    items = run_items(run([("kept.md", "/a/"), ("kept.md", "/a/"), ("deleted.md", "/b/")],
                          partial=[{"page": "p", "folder": "kept.md", "missing": ["fr", "de"]}]))
    assert len(items) == 5  # the same link twice in one file is two items; each missing language is one
    ledger = update({}, "r1", "2026-10-22", items, repos)
    ledger = update(ledger, "r2", "2026-10-29", {}, repos)
    c = counts(ledger, HOST)
    assert (c["fixed"], c["removed"], c["open"]) == (4, 1, 0)
    ledger = update(ledger, "r3", "2026-11-05", items, repos)  # all come back: reopened, deleted.md too
    c = counts(ledger, HOST)
    assert (c["fixed"], c["removed"], c["open"], c["new"]) == (0, 0, 5, 0)
    assert c["by_check"]["translation_partial"]["open"] == 2
