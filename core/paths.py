"""Where each tenant's files live: data/<tenant>/ (stores and caches, gitignored) and reports/<tenant>/ (run
reports, findings export, traffic summary, health history and archive; committed and published per tenant)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = REPO_ROOT / "data"
REPORTS_ROOT = REPO_ROOT / "reports"


@dataclass(frozen=True)
class TenantPaths:
    tenant: str
    data: Path
    reports: Path

    @property
    def inventory(self) -> Path:
        return self.data / "inventory.sqlite"

    @property
    def audit_db(self) -> Path:
        return self.data / "audit.sqlite"

    @property
    def findings_store(self) -> Path:
        return self.data / "findings.sqlite"

    @property
    def text_cache(self) -> Path:
        return self.data / "text-check-cache.json"

    @property
    def findings_export(self) -> Path:
        return self.reports / "findings.json"

    @property
    def traffic_summary(self) -> Path:
        return self.reports / "traffic-summary.json"

    @property
    def health_history(self) -> Path:
        return self.reports / "health-history.json"

    @property
    def archive(self) -> Path:
        return self.reports / "archive"


def tenant_paths(tenant: str, data_root: Path = DATA_ROOT, reports_root: Path = REPORTS_ROOT) -> TenantPaths:
    if not tenant or "/" in tenant or tenant.startswith("."):
        raise ValueError(f"Not a tenant name: {tenant!r}")
    return TenantPaths(tenant, data_root / tenant, reports_root / tenant)


def published_tenants(reports_root: Path = REPORTS_ROOT) -> list[str]:
    """Tenants with a findings export, i.e. a report to publish."""
    return sorted(p.parent.name for p in reports_root.glob("*/findings.json"))
