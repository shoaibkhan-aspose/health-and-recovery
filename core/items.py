"""Item ledger: the individual issues of the exact checks, tracked across runs, so a blog's report can say how many
were fixed, how many are new and how many are still open (SK, 2026-10-27).

An item is one thing to fix: one link (link check), one missing language of one post, one unsupported language.
Only exhaustive checks have items; sampled checks (crawl audit, text check, speed) stay at finding level, because a
page leaving the sample is not a fix. An item that is gone counts as fixed when its file still exists in the blog's
repo, and as removed when the file is gone (a deleted post is not a fix).

Ledger (reports/<tenant>/items.json): {"runs": [run refs], "baseline": {host: {"run", "date"}},
"items": {key: {"check", "host", "page", "file", "link", "first_seen", "last_seen", "fixed", "removed"}}}.
Shown as issues = fixed + open, fixed, open and the latest scan date (SK, 2026-10-27: no "new", no baseline on
the pages; new issues show as a growing issue count). Fixed % = fixed / issues; removed items count neither way.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path


def run_items(link_run: dict) -> dict[str, dict]:
    """key -> item for one link-check run. A link that appears twice in one file is two items."""
    items: dict[str, dict] = {}
    for host, h in (link_run.get("hosts") or {}).items():
        seen: Counter = Counter()
        for i in h.get("issues") or []:
            base = f"{i['check']}|{host}|{i['file']}|{i['link']}"
            seen[base] += 1
            items[f"{base}|{seen[base]}"] = {"check": i["check"], "host": host, "page": i.get("page"),
                                             "file": i["file"], "link": i["link"]}
        for p in h.get("partial") or []:
            for lang in p.get("missing") or []:
                items[f"translation_partial|{host}|{p['folder']}|{lang}"] = {
                    "check": "translation_partial", "host": host, "page": p.get("page"), "file": p["folder"],
                    "link": lang}
        for lang in h.get("unsupported") or {}:
            items[f"language_unsupported|{host}|{lang}"] = {"check": "language_unsupported", "host": host,
                                                           "page": None, "file": None, "link": lang}
    return items


def update(ledger: dict, run_ref: str, run_date: str, items: dict[str, dict], repos: dict[str, str]) -> dict:
    """Add one run (once). New keys open; open keys missing from the run are fixed if their file still exists
    in the host's repo (repos: host -> local clone, pulled to the run's commit), else removed; a key that comes
    back reopens."""
    if run_ref in ledger.setdefault("runs", []):
        return ledger
    known = ledger.setdefault("items", {})
    for host in {it["host"] for it in items.values()} | set(repos):
        ledger.setdefault("baseline", {}).setdefault(host, {"run": run_ref, "date": run_date})
    for key, it in items.items():
        rec = known.get(key)
        if rec is None:
            known[key] = {**it, "first_seen": run_date, "last_seen": run_date, "fixed": None, "removed": None}
        else:
            rec.update(last_seen=run_date, fixed=None, removed=None)
    for key, rec in known.items():
        if key in items or rec["fixed"] or rec["removed"] or rec["host"] not in repos:
            continue
        exists = not rec["file"] or (Path(repos[rec["host"]]) / rec["file"]).exists()
        rec["fixed" if exists else "removed"] = run_date
    ledger["runs"].append(run_ref)
    return ledger


def counts(ledger: dict, host: str) -> dict:
    """Since the host's baseline: items found at the baseline, new since, fixed, removed, still open, fixed %;
    the same per check."""
    base = (ledger.get("baseline") or {}).get(host)
    if not base:
        return {}

    def tally(recs: list[dict]) -> dict:
        fixed = sum(1 for r in recs if r["fixed"])
        still = sum(1 for r in recs if not r["fixed"] and not r["removed"])
        return {"issues": fixed + still, "fixed": fixed, "open": still,
                "removed": sum(1 for r in recs if r["removed"]),
                "baseline": sum(1 for r in recs if r["first_seen"] == base["date"]),
                "new": sum(1 for r in recs if r["first_seen"] > base["date"]),
                "fixed_pct": round(100 * fixed / (fixed + still)) if fixed + still else None}

    mine = [r for r in (ledger.get("items") or {}).values() if r["host"] == host]
    by_check: dict = {}
    for r in mine:
        by_check.setdefault(r["check"], []).append(r)
    scanned = max((d for r in mine for d in (r["last_seen"], r["fixed"], r["removed"]) if d), default=base["date"])
    return {**tally(mine), "since": base["date"], "scanned": scanned, "runs": len(ledger.get("runs") or []),
            "by_check": {c: tally(rs) for c, rs in sorted(by_check.items())}}
