"""Render the site health report (discovery, crawl audits, latest speed, AI and text checks) as one HTML page.

    python scripts/build_report.py                     # TENANT (default aspose.app): newest runs in reports/<tenant>/
    python scripts/build_report.py --tenant conholdate.cloud --out reports/conholdate.cloud/discovery-latest.html
    python scripts/build_report.py --findings reports/aspose.app/findings.json  # without the store (GitHub Actions)

The page embeds the run's data as JSON and renders it in the browser (no external requests except
Google Fonts). Findings come from the findings store (open + unverified, discovery and manual review,
with notes and what is new or resolved in this run); without a store, from the run JSON.
Optional --notes / --audit-notes: plain-text files, one key takeaway per line, shown at the top and in the
crawl audit section. --audit '' leaves the crawl audit out.
Output: reports/<tenant>/<input name>.html, or --out. GitHub Pages serves site/<tenant>/index.html (--standalone).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.findings import FindingsStore  # noqa: E402
from core.fix_guide import AREAS, GUIDE, guide_key  # noqa: E402
from core.paths import TenantPaths, tenant_paths  # noqa: E402
from core.report_view import (  # noqa: E402
    confidence,
    grade_summary,
    health_trend,
    merge_root_causes,
    run_changes,
    systemic_issues,
    top_risks,
)
from core.scorecard import family_of, family_scorecards, grade, scorecard  # noqa: E402
from core.tenant import load_tenant  # noqa: E402

TEMPLATE = REPO_ROOT / "scripts" / "templates" / "discovery_report.html"


# For hosting the file on its own (GitHub Pages): the artifact host adds this skeleton itself.
STANDALONE_HEAD = (
    '<!doctype html>\n<html lang="en">\n<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    '<meta name="robots" content="noindex, nofollow">\n'
)


# The Pages site has one shared fix guide page (scripts/build_site.py); a snapshot is served three levels below
# the site root (/<tenant>/archive/<date>/).
SNAPSHOT_GUIDE_URL = "../../../fix-guide/"


def build(report: dict, notes: list[str], run_date: str | None, standalone: bool = False,
          guide_url: str | None = None) -> str:
    """The report page. With guide_url, "How to fix" links go to that shared fix guide page instead of a full
    guide table inside the report (the table stays for local pages)."""
    data = dict(report, _notes=notes, _run_date=run_date, _guide_url=guide_url)
    if guide_url and report.get("_guide"):  # only the rows this report's findings link to
        used = {f.get("_guide") for f in report["findings"]}
        data["_guide"] = dict(report["_guide"], rows={k: v for k, v in report["_guide"]["rows"].items() if k in used})
    payload = json.dumps(data, default=str).replace("</", "<\\/")
    template = TEMPLATE.read_text(encoding="utf-8")
    page = template.replace("__TITLE__", f"{report['tenant']} Site Health").replace("__DATA__", payload)
    return STANDALONE_HEAD + page if standalone else page


def read_notes(path: str | None) -> list[str]:
    if not path:
        return []
    lines = [ln.strip().lstrip("-• ").strip() for ln in Path(path).read_text(encoding="utf-8").splitlines()]
    return [n for n in lines if n]


def run_date_of(path: Path, prefix: str) -> str | None:
    return path.stem.split("-run")[0].removeprefix(prefix) if "-run" in path.stem else None


def runs_by_name(pattern: str, folder: Path) -> list[Path]:
    """Report files in `folder` ordered by the date and run number in their names (file times are meaningless
    after a git checkout)."""
    def key(p: Path):
        m = re.search(r"(\d{4}-\d{2}-\d{2})-run(\d+)", p.name)
        return (m.group(1), int(m.group(2))) if m else ("", 0)
    return sorted(folder.glob(pattern), key=key)


def newest(pattern: str, folder: Path) -> Path | None:
    found = runs_by_name(pattern, folder)
    return found[-1] if found else None


def from_export(rows: list[dict], run_ref: str) -> tuple[list[dict], dict]:
    """Open findings and what `run_ref` changed, from reports/<tenant>/findings.json (for CI, where the store is
    absent).
    Same shape as FindingsStore.findings() and .changes()."""
    findings = sorted((f for f in rows if f["status"] in ("open", "unverified")), key=lambda f: f["id"])
    def seq(ref: str | None) -> int:  # discovery-run7 -> 7
        m = re.search(r"(\d+)$", ref or "")
        return int(m.group(1)) if m else 0

    prefix = run_ref.rstrip("0123456789")
    first_run = not any((f.get("first_seen_run") or "").startswith(prefix) and seq(f["first_seen_run"]) < seq(run_ref)
                        for f in rows)
    new = [f["id"] for f in findings if f.get("first_seen_run") == run_ref]
    resolved = [f for f in rows if f["status"] == "resolved" and f.get("resolved_run") == run_ref]
    return findings, {"run": run_ref, "new": [] if first_run else new, "resolved": resolved, "first_run": first_run}


def audit_summary(paths: list[Path], notes: list[str]) -> dict:
    """Merge crawl-audit runs (oldest first): the newest result wins for a page checked more than once, so
    section-scoped batches add up to one picture. Only what the page shows (not per-page facts)."""
    from core.audit.crawl import summarize_pages

    pages: dict = {}
    runs = []
    for path in paths:
        a = json.loads(path.read_text(encoding="utf-8"))
        runs.append({"run_id": a["run_id"], "date": run_date_of(path, "crawl-audit-"), "pages": len(a["pages"]),
                     "sections": a["params"].get("sections"), "requests": a["requests"],
                     "duration_s": a["duration_s"], "stopped_early": a["stopped_early"]})
        for p in a["pages"]:
            pages[p["url"]] = p
    last = json.loads(paths[-1].read_text(encoding="utf-8"))
    sections: dict = {}
    for p in pages.values():
        if p["section"]:
            sections.setdefault(p["host"], set()).add(p["section"])
    return {"run_id": runs[-1]["run_id"], "date": runs[-1]["date"], "runs": runs, "params": last["params"],
            "summary": summarize_pages(list(pages.values())), "pages": len(pages),
            "requests": sum(r["requests"] for r in runs), "duration_s": sum(r["duration_s"] for r in runs),
            "stopped_early": any(r["stopped_early"] for r in runs),
            "sections": {h: sorted(v) for h, v in sections.items()}, "notes": notes}


def add_families(report: dict, summary_path: Path) -> None:
    """Family scorecards per sectioned host (from reports/<tenant>/traffic-summary.json, written by prioritize.py), and
    each finding's family for the report's Family filter."""
    if not summary_path.is_file():
        return
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    report["_families"] = {"windows": summary["windows"], "hosts": {}}
    for host, traffic in summary["hosts"].items():
        families = list(traffic)
        report["_families"]["hosts"][host] = family_scorecards(
            report["findings"], host, families, (report.get("sections") or {}).get(host) or {}, traffic)
        for f in report["findings"]:
            fam = family_of(f, host, set(families))
            if fam:
                f["_family"] = fam
    if not report["_families"]["hosts"]:  # a tenant without sectioned hosts has no family cards
        del report["_families"]


def family_rollup(host: str, cards: list[dict], findings: list[dict], urls: int) -> dict:
    """Health of a host with family cards: its families' health weighted by search clicks (equal weights without
    traffic), minus the points of host-wide findings that belong to no family (root sitemap, robots.txt, ...).
    The lowest family is named, because an average hides a failing one."""
    clicks = {c["family"]: ((c.get("traffic") or {}).get("current") or {}).get("clicks") or 0 for c in cards}
    total = sum(clicks.values())
    weighted = (sum(c["health"] * clicks[c["family"]] for c in cards) / total if total
                else sum(c["health"] for c in cards) / len(cards))
    families = {c["family"] for c in cards}
    host_wide = scorecard(host, [f for f in findings if not family_of(f, host, families)], urls)
    health = max(0, round(weighted - (100 - host_wide["health"])))
    low = min(cards, key=lambda c: c["health"])
    return {"health": health, "grade": grade(health), "penalties": host_wide["penalties"],
            "cosmetic_capped": host_wide["cosmetic_capped"],
            "rollup": {"families": len(cards), "weighted": round(weighted), "host_wide": 100 - host_wide["health"],
                       "by_clicks": bool(total), "lowest": {"family": low["family"], "grade": low["grade"],
                                                           "health": low["health"]}}}


def add_hosts(report: dict, summary_path: Path) -> None:
    """A health row per host (same score as families, whole host as the scope), with the host's search traffic
    from reports/<tenant>/traffic-summary.json. A host with family cards rolls its families up (family_rollup)."""
    family_cards = (report.get("_families") or {}).get("hosts") or {}
    totals = {}
    if summary_path.is_file():
        totals = json.loads(summary_path.read_text(encoding="utf-8")).get("host_totals") or {}
    cards = []
    for host, stats in sorted((report.get("hosts") or {}).items()):
        mine = [f for f in report["findings"] if f["subdomain"] == host]
        card = scorecard(host, mine, stats.get("urls") or 0)
        if family_cards.get(host):
            card.update(family_rollup(host, family_cards[host], mine, stats.get("urls") or 0))
        card.update(host=card.pop("family"), urls=stats.get("urls") or 0, traffic=totals.get(host) or {})
        cards.append(card)
    if cards:
        report["_hosts"] = cards


def drop_hosts(report: dict, keep: set) -> list[str]:
    """Leave out hosts the tenant no longer audits (dropped from its YAML after a run): run files are never
    edited, so their per-host data is filtered here. Returns the hosts left out."""
    audit, ai, perf, text = (report.get(k) or {} for k in ("_audit", "_ai", "_perf", "_text"))
    tables = [report.get("hosts"), audit.get("summary"), ai.get("summary"), perf.get("summary"),
              perf.get("origins"), text.get("summary")]
    dropped: set = set()
    for table in tables:
        if isinstance(table, dict):
            for h in [h for h in table if h not in keep]:
                dropped.add(h)
                del table[h]
    return sorted(dropped)


def runs_label(report: dict) -> str:
    runs = f"discovery run {report['run_id']}"
    if report.get("_audit"):
        runs += f", crawl audit run {report['_audit']['run_id']}"
    if report.get("_perf"):
        runs += f", speed audit run {report['_perf']['run_id']}"
    if report.get("_ai"):
        runs += f", AI audit run {report['_ai']['run_id']}"
    if report.get("_text"):
        runs += f", text check run {report['_text']['run_id']}"
    return runs


def perf_summary(path: Path) -> dict:
    """What the page shows of a speed-audit run: run id, date, scope, per-host summary, whole-host field data."""
    a = json.loads(path.read_text(encoding="utf-8"))
    return {"run_id": a["run_id"], "date": run_date_of(path, "perf-audit-"), "params": a["params"],
            "pages": len(a["pages"]), "summary": a["summary"], "origins": a["origins"],
            "stopped_early": a["stopped_early"], "incomplete": a.get("incomplete", False)}


def ai_summary(path: Path) -> dict:
    """What the page shows of an AI-audit run: run id, date, per-host access and llms.txt summary."""
    a = json.loads(path.read_text(encoding="utf-8"))
    return {"run_id": a["run_id"], "date": run_date_of(path, "ai-audit-"), "summary": a["summary"]}


def text_summary(path: Path) -> dict:
    """What the page shows of a text-check run: run id, date, model, pages checked, per-host summary."""
    a = json.loads(path.read_text(encoding="utf-8"))
    return {"run_id": a["run_id"], "date": run_date_of(path, "text-audit-"), "model": a["params"]["model"],
            "pages_checked": a["pages_checked"], "unchecked": len(a["unchecked"]), "summary": a["summary"]}


def add_views(report: dict, all_rows: list[dict] | None = None, history_path: Path | None = None) -> None:
    """Fix guide, confidence, merged root causes, systemic issues, top risks, grade summary, changes since the
    last run and the family health trend (see core/report_view.py)."""
    findings = report["findings"]
    report["_guide"] = {"areas": AREAS, "rows": GUIDE}
    for f in findings:
        f["_guide"] = guide_key(f.get("check"), f.get("source"))
        f["_confidence"] = confidence(f)
    refs = [f"discovery-run{report['run_id']}"]
    if report.get("_audit"):
        refs.append(f"crawl-audit-run{report['_audit']['run_id']}")
    if report.get("_perf"):
        refs.append(f"perf-audit-run{report['_perf']['run_id']}")
    if report.get("_ai"):
        refs.append(f"ai-audit-run{report['_ai']['run_id']}")
    if report.get("_text"):
        refs.append(f"text-audit-run{report['_text']['run_id']}")
    changes = run_changes(all_rows or findings, refs)
    titles = {f["id"]: f for f in (all_rows or findings)}
    changes["resolved"] = [{"id": i, "title": titles[i]["title"], "subdomain": titles[i]["subdomain"]}
                           for i in changes["resolved"]]
    report["_run_changes"] = changes
    fam = report.get("_families")
    if not fam:
        return
    host = next(iter(fam["hosts"]))
    cards = fam["hosts"][host]
    families = {c["family"] for c in cards}
    merge_root_causes(findings, host, families)
    report["_systemic"] = systemic_issues(findings, host, families)
    report["_risks"] = top_risks(findings, host, families)
    report["_summary"] = grade_summary(cards)
    history = json.loads(history_path.read_text(encoding="utf-8")) if history_path and history_path.is_file() else []
    report["_trend"] = health_trend(cards, history, runs_label(report))


def record_health(report: dict, history_path: Path) -> None:
    """Append this run's family health to reports/<tenant>/health-history.json (once per set of runs)."""
    from datetime import date

    fam = report.get("_families")
    if not fam:
        return
    history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.is_file() else []
    runs = runs_label(report)
    if any(h["runs"] == runs for h in history):
        return
    cards = fam["hosts"][next(iter(fam["hosts"]))]
    history.append({"date": date.today().isoformat(), "runs": runs,
                    "families": {c["family"]: c["health"] for c in cards}})
    history_path.write_text(json.dumps(history, indent=1) + "\n", encoding="utf-8")


def snapshot(page: str, label: str, report: dict, archive: Path) -> Path:
    """Freeze a standalone report page into reports/<tenant>/archive/<date>[-n].html and list it in index.json.
    Snapshots are never rebuilt (older reports stay as they were)."""
    from datetime import date

    archive.mkdir(parents=True, exist_ok=True)
    day = date.today().isoformat()
    name, n = f"{day}.html", 1
    while (archive / name).exists():
        n += 1
        name = f"{day}-{n}.html"
    (archive / name).write_text(page, encoding="utf-8")
    index_path = archive / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file() else []
    runs = runs_label(report)
    index.append({"file": name, "date": day if n == 1 else f"{day} ({n})", "label": label, "runs": runs})
    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return archive / name


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("report", nargs="?", help="discovery JSON (default: newest in reports/<tenant>/)")
    ap.add_argument("--tenant", default=os.environ.get("TENANT", "aspose.app"))
    ap.add_argument("--notes", help="text file, one key takeaway per line")
    ap.add_argument("--audit", help="comma-separated crawl-audit JSONs, merged oldest first "
                    "(default: every crawl-audit run in reports/<tenant>/; '' to leave out)")
    ap.add_argument("--audit-notes", help="text file, one crawl-audit takeaway per line")
    ap.add_argument("--perf", help="speed-audit JSON (default: newest perf-audit run in reports/<tenant>/; "
                    "'' to leave out)")
    ap.add_argument("--ai", help="AI-audit JSON (default: newest ai-audit run in reports/<tenant>/; '' to leave out)")
    ap.add_argument("--text", help="text-check JSON (default: newest text-audit run in reports/<tenant>/; "
                    "'' to leave out)")
    ap.add_argument("--out", help="output HTML path")
    ap.add_argument("--guide-url", help="link to the shared fix guide page instead of including the guide table "
                    "(build_site.py passes ../fix-guide/)")
    ap.add_argument("--standalone", action="store_true",
                    help="add doctype, charset, viewport and noindex (for GitHub Pages: --out docs/index.html)")
    ap.add_argument("--store", help="findings store (default: data/<tenant>/findings.sqlite)")
    ap.add_argument("--findings", help="read findings from this export (reports/<tenant>/findings.json) instead of "
                    "the store")
    ap.add_argument("--snapshot", metavar="LABEL",
                    help="also freeze this report (standalone) into reports/<tenant>/archive/ with this label, and "
                    "record the family health in reports/<tenant>/health-history.json (do this after every full run)")
    args = ap.parse_args(argv)
    paths: TenantPaths = tenant_paths(args.tenant)
    folder = paths.reports

    if args.report:
        src = Path(args.report)
    else:
        src = newest("discovery-*-run*.json", folder)
        if src is None:
            print(f"No discovery-*.json in {folder}. Run scripts/discover.py first.", file=sys.stderr)
            return 1

    report = json.loads(src.read_text(encoding="utf-8"))
    notes = read_notes(args.notes)
    all_rows: list[dict] | None = None
    audit_srcs: list[Path] = []
    if args.audit:
        audit_srcs = [Path(a) for a in args.audit.split(",")]
    elif args.audit is None:
        audit_srcs = runs_by_name("crawl-audit-*-run*.json", folder)
    audit_src = audit_srcs[-1] if audit_srcs else None
    if audit_srcs:
        report["_audit"] = audit_summary(audit_srcs, read_notes(args.audit_notes))
    perf_src = Path(args.perf) if args.perf else newest("perf-audit-*-run*.json", folder) if args.perf is None else None
    if perf_src:
        report["_perf"] = perf_summary(perf_src)
    ai_src = Path(args.ai) if args.ai else newest("ai-audit-*-run*.json", folder) if args.ai is None else None
    if ai_src:
        report["_ai"] = ai_summary(ai_src)
    text_src = Path(args.text) if args.text else newest("text-audit-*-run*.json", folder) if args.text is None else None
    if text_src:
        report["_text"] = text_summary(text_src)
    if args.findings:
        rows = json.loads(Path(args.findings).read_text(encoding="utf-8"))
        all_rows = rows
        report["findings"], report["_changes"] = from_export(rows, f"discovery-run{report['run_id']}")
        if not audit_src:
            report["findings"] = [f for f in report["findings"] if f["source"] != "crawl-audit"]
        if not perf_src:
            report["findings"] = [f for f in report["findings"] if f["source"] != "perf-audit"]
        if not ai_src:
            report["findings"] = [f for f in report["findings"] if f["source"] != "ai-audit"]
        if not text_src:
            report["findings"] = [f for f in report["findings"] if f["source"] != "text-audit"]
    elif Path(store_path := args.store or str(paths.findings_store)).is_file():
        store = FindingsStore(store_path)
        run_ref = f"discovery-run{report['run_id']}"
        if not store.db.execute("SELECT 1 FROM runs WHERE ref = ?", (run_ref,)).fetchone():
            print(f"{run_ref} is not in the findings store; run scripts/findings.py import-discovery {src}",
                  file=sys.stderr)
            return 1
        report["findings"] = store.findings()
        all_rows = store.findings(("open", "unverified", "resolved", "superseded"))
        report["_changes"] = store.changes(run_ref)
        if not audit_src:
            report["findings"] = [f for f in report["findings"] if f["source"] != "crawl-audit"]
        if not perf_src:
            report["findings"] = [f for f in report["findings"] if f["source"] != "perf-audit"]
        if not ai_src:
            report["findings"] = [f for f in report["findings"] if f["source"] != "ai-audit"]
        if not text_src:
            report["findings"] = [f for f in report["findings"] if f["source"] != "text-audit"]
    else:
        print(f"No findings store at {store_path}; using the run's own findings.", file=sys.stderr)
    try:
        dropped = drop_hosts(report, set(load_tenant(args.tenant).host_names))
    except FileNotFoundError:
        dropped = []
    if dropped:
        print(f"Left out hosts no longer in tenants/{args.tenant}.yaml: {', '.join(dropped)}", file=sys.stderr)
    add_families(report, paths.traffic_summary)
    add_hosts(report, paths.traffic_summary)
    add_views(report, all_rows, paths.health_history)
    run_date = run_date_of(src, "discovery-")

    out = Path(args.out) if args.out else src.with_suffix(".html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(report, notes, run_date, args.standalone, args.guide_url), encoding="utf-8")
    if args.snapshot:
        page = build(report, notes, run_date, standalone=True, guide_url=SNAPSHOT_GUIDE_URL)
        print(f"Snapshot: {snapshot(page, args.snapshot, report, paths.archive)}")
        record_health(report, paths.health_history)
    print(f"Wrote {out} ({out.stat().st_size / 1024:.0f} KB) from {src.name}"
          + (f" and {len(audit_srcs)} crawl-audit runs (newest {audit_src.name})" if audit_src else "")
          + (f" and {perf_src.name}" if perf_src else "") + (f" and {ai_src.name}" if ai_src else "")
          + (f" and {text_src.name}" if text_src else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
