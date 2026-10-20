"""Text check: page selection, answer validation, cache, findings, import and scoring. Offline (fake LLM)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.audit.store import AuditStore  # noqa: E402
from core.audit.text import (  # noqa: E402
    TextAudit,
    TextCache,
    import_report,
    merge_crawl_pages,
    page_item,
    valid_issue,
)
from core.findings import FindingsStore  # noqa: E402
from core.llm import LLMError, parse_json  # noqa: E402
from core.prioritize import TrafficIndex, finding_traffic  # noqa: E402
from core.scorecard import family_of, group_of  # noqa: E402
from core.tenant import Tenant  # noqa: E402


def make_tenant(ignore=()):
    raw = {"property": "example.com", "excluded_host_patterns": [], "audit": {"text": {"ignore": list(ignore)}},
           "hosts": [{"host": "www.example.com"}, {"host": "app.example.com", "fix_channel": "git-pr"}],
           "discovery": {"section_sitemaps": {"app.example.com": {"sections": ["pdf", "omr"], "paths": []}}}}
    return Tenant(property="example.com", gsc_properties=["https://app.example.com/"], ga4_property_id=None,
                  hosts=raw["hosts"], excluded_host_patterns=[], raw=raw)


def page(url, title, h1=None, desc=None, section="", host=None, status=200, issues=(), lang=None):
    host = host or url.split("/")[2]
    return {"url": url, "host": host, "section": section, "lang": lang, "status": status, "ignored": None,
            "issues": [list(i) for i in issues],
            "facts": {"titles": [title] if title else [], "h1": [h1] if h1 else [],
                      "descriptions": [desc] if desc else [], "html_lang": "en"}}


PAGES = [
    page("https://app.example.com/omr/family/", "Apps to Generate And Recoginze OMR Files", "OMR apps",
         section="omr"),
    page("https://app.example.com/pdf/", "Apps to view, edit test PDF", "Apps to view, edit test PDF", section="pdf"),
    page("https://app.example.com/pdf/merger", "Merge PDF", "Merge PDF files", section="pdf"),
    page("https://www.example.com/", "Free apps for for files", "Free apps"),
    page("https://app.example.com/pdf/gone", "Page not found", section="pdf", issues=[("soft_404", "x")]),
    page("https://app.example.com/pdf/err", "Error", section="pdf", status=500),
]


class FakeLLM:
    """Answers from a table {path: [issues]}; records what it was sent."""

    model = "fake-1"

    def __init__(self, answers, fail=0, reply=None):
        self.answers, self.fail, self.reply, self.sent = answers, fail, reply, []

    def complete(self, system, user):
        items = json.loads(user)
        self.sent.append(items)
        if self.fail:
            self.fail -= 1
            raise LLMError("timeout")
        if self.reply is not None:
            return self.reply
        issues = [{"id": it["id"], **i} for it in items for i in self.answers.get(it["path"], [])]
        return "Here you go:\n```json\n" + json.dumps({"issues": issues}) + "\n```"

    class usage:  # noqa: N801 - mimics core.llm.Usage
        @staticmethod
        def as_dict():
            return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}


ANSWERS = {
    "app.example.com/omr/family/": [{"field": "title", "kind": "misspelling", "wrong": "Recoginze",
                                     "fix": "Recognize"}],
    "app.example.com/pdf/": [{"field": "title", "kind": "stray_word", "wrong": "test", "fix": ""},
                             {"field": "h1", "kind": "stray_word", "wrong": "test", "fix": ""}],
    "www.example.com/": [{"field": "title", "kind": "repeated_word", "wrong": "for for", "fix": "for"}],
    # invented: the word is not on the page -> dropped
    "app.example.com/pdf/merger": [{"field": "h1", "kind": "misspelling", "wrong": "Mrege", "fix": "Merge"}],
}


def run(llm, cache=None, ignore=(), **kw):
    store = AuditStore(":memory:")
    return TextAudit(make_tenant(ignore), llm, store, PAGES, cache or TextCache(None), log=None, **kw).run()


def test_page_item_skips_errors_soft_404_and_empty_pages():
    assert page_item(PAGES[0])["path"] == "app.example.com/omr/family/"
    assert page_item(PAGES[0])["lang"] == "en"  # from html lang
    assert page_item(PAGES[4]) is None and page_item(PAGES[5]) is None
    assert page_item(page("https://app.example.com/x", None)) is None


def test_valid_issue_needs_the_wrong_text_on_the_page():
    item = page_item(PAGES[3])
    assert valid_issue({"field": "title", "kind": "repeated_word", "wrong": "for for", "fix": "for"}, item)
    assert valid_issue({"field": "title", "kind": "repeated_word", "wrong": "apps for", "fix": "for"}, item) is None
    assert valid_issue({"field": "h1", "kind": "misspelling", "wrong": "for for", "fix": "for"}, item) is None
    assert valid_issue({"field": "title", "kind": "style", "wrong": "Free", "fix": "free"}, item) is None
    assert valid_issue({"field": "title", "kind": "misspelling", "wrong": "Free", "fix": "Free"}, item) is None


def test_numbers_and_topics_the_h1_shares_are_dropped():
    item = page_item(page("https://m.example.com/x", "Usage 1,234 of AI files", "AI to PNG", "Convert PSD files"))
    assert valid_issue({"field": "title", "kind": "misspelling", "wrong": "1,234", "fix": "x"}, item) is None
    assert valid_issue({"field": "title", "kind": "topic_mismatch", "wrong": "AI", "fix": "PNG"}, item) is None
    assert valid_issue({"field": "description", "kind": "topic_mismatch", "wrong": "PSD", "fix": "AI"}, item)


def test_reviewed_false_positives_are_ignored():
    report = run(FakeLLM(ANSWERS), ignore=[{"url": "https://www.example.com/", "wrong": "FOR FOR"},
                                           {"url": "https://app.example.com/pdf/"}])
    assert {f["template"] for f in report["findings"]} == {"page:omr"} and report["ignored"] == 3


def test_parse_json_tolerates_prose_and_fences():
    assert parse_json('Sure.\n```json\n{"issues": []}\n```') == {"issues": []}


def test_findings_grouped_per_section_with_exact_pages():
    report = run(FakeLLM(ANSWERS))
    by = {(f["subdomain"], f["template"]): f for f in report["findings"]}
    assert set(by) == {("app.example.com", "page:omr"), ("app.example.com", "page:pdf"), ("www.example.com", "page")}
    pdf = by[("app.example.com", "page:pdf")]
    assert pdf["check"] == "text_leftover" and pdf["dimension"] == "onpage" and pdf["type"] == "incorrect"
    omr = by[("app.example.com", "page:omr")]
    assert omr["check"] == "text_typo" and omr["type"] == "improvable"
    assert pdf["affected_pages"] == 1 and pdf["evidence"]["urls"] == ["https://app.example.com/pdf/"]
    assert pdf["evidence"]["observed"].startswith("1 of 2 checked pages")  # merger checked, error pages not
    assert [loc["note"] for loc in pdf["evidence"]["locations"]] == ["title: remove 'test'", "H1: remove 'test'"]
    assert pdf["title"].endswith("(/pdf/)") and pdf["fix_channel"] == "git-pr"
    assert report["pages_checked"] == 4 and not report["incomplete"]


def test_cache_sends_only_new_text(tmp_path):
    cache_file = tmp_path / "cache.json"
    first = FakeLLM(ANSWERS)
    run(first, TextCache(cache_file))
    assert sum(len(b) for b in first.sent) == 4
    second = FakeLLM({})  # would find nothing; the cache must answer instead
    report = run(second, TextCache(cache_file))
    assert second.sent == [] and report["llm"]["cache_hits"] == 4 and len(report["findings"]) == 3


def test_failed_batches_make_the_run_incomplete_and_resolve_nothing():
    llm = FakeLLM(ANSWERS, fail=2)  # the only batch fails twice
    report = run(llm)
    assert report["incomplete"] and len(report["unchecked"]) == 4 and report["findings"] == []
    store = FindingsStore(":memory:")
    import_report(store, run(FakeLLM(ANSWERS)))
    changes = import_report(store, report)
    assert changes["resolved"] == [] and len(store.findings()) == 3


def test_a_retry_rescues_one_failure_and_bad_json_is_a_failure():
    assert not run(FakeLLM(ANSWERS, fail=1))["incomplete"]
    assert run(FakeLLM({}, reply="no json here"))["incomplete"]


def test_import_resolves_fixed_typos():
    store = FindingsStore(":memory:")
    import_report(store, run(FakeLLM(ANSWERS)))
    fixed = {k: v for k, v in ANSWERS.items() if k != "www.example.com/"}
    changes = import_report(store, run(FakeLLM(fixed)))
    assert len(changes["resolved"]) == 1 and len(store.findings()) == 2


def test_scored_on_exact_pages_and_grouped_as_wording():
    f = {**next(f for f in run(FakeLLM(ANSWERS))["findings"] if f["template"] == "page:omr"), "source": "text-audit"}
    rows = [{"page": "https://app.example.com/omr/family/", "clicks": 7, "impressions": 300},
            {"page": "https://app.example.com/omr/scan", "clicks": 900, "impressions": 9000}]
    idx = TrafficIndex.build(make_tenant(), rows)
    t = finding_traffic(f, idx)
    assert (t["clicks_28d"], t["impressions_28d"], t["source"]) == (7, 300, "gsc-pages")
    assert group_of(f) == "wording" and family_of(f, "app.example.com", {"pdf", "omr"}) == "omr"


def test_merge_crawl_pages_newest_wins():
    old = {"pages": [page("https://a.example.com/", "Old")]}
    new = {"pages": [page("https://a.example.com/", "New")]}
    assert merge_crawl_pages([old, new])[0]["facts"]["titles"] == ["New"]
