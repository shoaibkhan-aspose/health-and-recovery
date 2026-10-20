"""Read-only Google credentials. Phase 1 refuses any scope that could write."""

from __future__ import annotations

import os

GSC_READONLY = "https://www.googleapis.com/auth/webmasters.readonly"
GA4_READONLY = "https://www.googleapis.com/auth/analytics.readonly"
READONLY_SCOPES = (GSC_READONLY, GA4_READONLY)
# PageSpeed Insights accepts any token and only needs to know the calling project: an identity-only scope.
IDENTITY_SCOPES = ("openid",)


class CredentialsError(RuntimeError):
    pass


def assert_readonly(scopes: tuple[str, ...] | list[str]) -> None:
    bad = [s for s in scopes if not s.endswith(".readonly") and s not in IDENTITY_SCOPES]
    if bad:
        raise CredentialsError(f"Phase 1 is read-only; refusing write-capable scopes: {bad}")


DEFAULT_KEY_ENV = "GOOGLE_APPLICATION_CREDENTIALS"


def get_credentials(scopes: tuple[str, ...] = READONLY_SCOPES, key_env: str = DEFAULT_KEY_ENV):
    """Return (credentials, identity) for the service-account key named by the env variable `key_env`.

    The default variable uses Application Default Credentials; a tenant can name another variable
    (tenant YAML `google_credentials_env`) so each tenant uses its own service account. There is no
    fallback to the default key: a missing variable is an error.
    Never logs or returns key material; `identity` is only the service-account email.
    """
    assert_readonly(scopes)

    key_path = os.environ.get(key_env)
    if not key_path:
        raise CredentialsError(
            f"{key_env} is not set. Point it at the service-account key (stored outside this repo)."
        )
    if not os.path.isfile(key_path):
        raise CredentialsError(f"{key_env} points to a file that does not exist.")

    if key_env == DEFAULT_KEY_ENV:
        import google.auth  # imported late so offline tests don't need the package

        credentials, _project = google.auth.default(scopes=list(scopes))
    else:
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_file(key_path, scopes=list(scopes))
    identity = getattr(credentials, "service_account_email", None) or "unknown"
    return credentials, identity
