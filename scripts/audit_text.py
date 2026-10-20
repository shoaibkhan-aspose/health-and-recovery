"""Text check: spelling and topic errors in titles, H1s and meta descriptions, proofread by an LLM.

Run on the Mac after the crawl audit (needs the LLM settings in .env; no requests to the site):

    python scripts/audit_text.py                 # every crawl-audit run in reports/, newest result per page
    python scripts/audit_text.py --plan          # pages, cached answers and LLM calls it would make
    python scripts/audit_text.py --hosts products.aspose.app

Answers are cached per page text in data/text-check-cache.json, so a rerun sends only new or changed text.
The LLM endpoint is shared with other teams: --workers calls at once (default 4), and all calls pause 30 s after
a failure (rate limit, timeout, server error).

Writes the report to reports/text-audit-<date>-run<id>.json and the findings to the findings store (exported to
reports/findings.json). Run prioritize.py afterwards to score the new findings.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.audit.store import AuditStore  # noqa: E402
from core.audit.text import TextCache, audit, import_report, merge_crawl_pages, page_item  # noqa: E402
from core.findings import FindingsStore  # noqa: E402
from core.llm import DEFAULT_CONCURRENCY, ENV_MODEL, LLM, LLMError  # noqa: E402
from core.tenant import load_tenant  # noqa: E402
from scripts.build_report import runs_by_name  # noqa: E402
from scripts.findings import DEFAULT_STORE, export, import_seed, print_changes  # noqa: E402

DEFAULT_CACHE = REPO_ROOT / "data" / "text-check-cache.json"


def _csv(value: str | None) -> list[str] | None:
    return [v.strip() for v in value.split(",") if v.strip()] if value else None


def print_summary(report: dict) -> None:
    llm = report["llm"]
    print(f"\n== Pages ({report['pages_checked']} checked, {len(report['unchecked'])} unchecked, "
          f"{llm['calls']} LLM calls, {llm['cache_hits']} cached, {report['duration_s']}s) ==")
    for host, h in report["summary"].items():
        issues = ", ".join(f"{k} {v}" for k, v in sorted(h["issues"].items())) or "none"
        print(f"  {host:28} checked={h['checked']:<4} pages with issues={h['pages_with_issues']:<3} {issues}")
    if report["incomplete"]:
        print("  INCOMPLETE: too many pages unchecked; this run resolves nothing")
    findings = report["findings"]
    print(f"\n== Findings ({len(findings)}) ==")
    for f in findings:
        print(f"  {f['id']:<14} {f['type']:<10} {f['subdomain']:<24} {f['title']}  [{f['affected_pages']} pages]")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tenant", default=os.environ.get("TENANT", "aspose.app"))
    ap.add_argument("--crawl", help="comma-separated crawl-audit JSONs, oldest first (default: all in reports/)")
    ap.add_argument("--hosts", help="comma-separated hosts (default: every host in the crawl audit)")
    ap.add_argument("--batch", type=int, default=20, help="pages per LLM call")
    ap.add_argument("--workers", type=int, default=DEFAULT_CONCURRENCY, help="parallel LLM calls")
    ap.add_argument("--cache", default=str(DEFAULT_CACHE), help="answer cache ('' for none)")
    ap.add_argument("--plan", action="store_true", help="show what would be sent, call nothing")
    ap.add_argument("--db", default=str(REPO_ROOT / "data" / "audit.sqlite"))
    ap.add_argument("--out-dir", default=str(REPO_ROOT / "reports"))
    ap.add_argument("--findings-store", default=str(DEFAULT_STORE), help="'' to skip the findings store")
    ap.add_argument("--quiet", action="store_true", help="no per-batch progress lines")
    args = ap.parse_args(argv)

    tenant = load_tenant(args.tenant)
    srcs = [Path(p) for p in _csv(args.crawl)] if args.crawl else runs_by_name("crawl-audit-*-run*.json")
    if not srcs:
        print("No crawl-audit runs in reports/. Run scripts/audit_crawl.py first.", file=sys.stderr)
        return 1
    reports = [json.loads(p.read_text(encoding="utf-8")) for p in srcs]
    pages = merge_crawl_pages(reports)
    hosts = _csv(args.hosts)
    cache = TextCache(args.cache or None)

    if args.plan:
        model = os.environ.get(ENV_MODEL, "")
        items = [it for p in pages if (not hosts or p["host"] in hosts) and (it := page_item(p))]
        cached = sum(1 for it in items if TextCache.key(it, model) in cache.data)
        todo = len(items) - cached
        print(f"{len(pages)} crawl-audit pages, {len(items)} with copy to check; {cached} cached; "
              f"{todo} to send in {-(-todo // args.batch)} LLM calls (model {model or 'not set'})")
        return 0

    try:
        llm = LLM.from_env(concurrency=args.workers)
    except LLMError as e:
        print(f"LLM: {e}", file=sys.stderr)
        return 2
    report = audit(tenant, llm, AuditStore(args.db), pages, cache, hosts=hosts,
                   crawl_runs=[r["run_id"] for r in reports], batch=args.batch,
                   log=None if args.quiet else print)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"text-audit-{date.today().isoformat()}-run{report['run_id']}.json"
    out_file.write_text(json.dumps(report, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    print_summary(report)
    print(f"\nFull report: {out_file}")
    if args.findings_store:
        fstore = FindingsStore(args.findings_store)
        import_seed(fstore, tenant.property)
        print(f"\nFindings store: {args.findings_store}")
        print_changes(import_report(fstore, report))
        print(f"Findings export: {export(fstore)}  (run prioritize.py to score them)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
