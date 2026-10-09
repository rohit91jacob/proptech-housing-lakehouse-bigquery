"""Cheap BigQuery credential check run before every scheduled load.

It lists one dataset and dry-runs ``SELECT 1``. Neither is billed or stored. Failures are
translated into the credential to fix, so the alert issue says what to do instead of
only quoting a stack trace.
"""

from __future__ import annotations

from dataclasses import dataclass

from google.api_core import exceptions as gexc
from google.auth import exceptions as auth_exc
from google.cloud import bigquery


@dataclass(frozen=True)
class Health:
    ok: bool
    summary: str
    fix: str = ""


AUTH_FIX = (
    "Renew the BigQuery credential: with WIF, check the GCP_WORKLOAD_IDENTITY_PROVIDER / "
    "GCP_SERVICE_ACCOUNT variables and the pool's attribute condition; with a key, the "
    "GCP_SA_KEY secret may have been revoked or deleted. Create a new JSON key for the "
    "service account and replace the secret (docs/gcp_setup.md)."
)
ROLE_FIX = (
    "The credential works but lacks permissions: grant the service account "
    "roles/bigquery.dataEditor and roles/bigquery.jobUser on the project."
)
API_FIX = "Enable the BigQuery API for the project (APIs & Services -> Library -> BigQuery API)."


def diagnose(exc: BaseException) -> Health:
    text = str(exc)
    if isinstance(exc, (auth_exc.DefaultCredentialsError, auth_exc.RefreshError)):
        return Health(False, f"credentials rejected: {text[:300]}", AUTH_FIX)
    if isinstance(exc, gexc.Unauthorized):
        return Health(False, f"unauthenticated: {text[:300]}", AUTH_FIX)
    if isinstance(exc, gexc.Forbidden):
        if "has not been used" in text or "is disabled" in text or "SERVICE_DISABLED" in text:
            return Health(False, f"BigQuery API disabled: {text[:300]}", API_FIX)
        return Health(False, f"permission denied: {text[:300]}", ROLE_FIX)
    if isinstance(exc, gexc.NotFound):
        return Health(False, f"project not found: {text[:300]}", "Check GCP_PROJECT_ID.")
    return Health(False, f"{type(exc).__name__}: {text[:300]}", "See the run log.")


def check(project: str | None, location: str, client: bigquery.Client | None = None) -> Health:
    try:
        bq = client or bigquery.Client(project=project, location=location)
        list(bq.list_datasets(max_results=1))
        job = bq.query("SELECT 1", job_config=bigquery.QueryJobConfig(dry_run=True))
        processed = job.total_bytes_processed or 0
        return Health(True, f"BigQuery reachable in project {bq.project} (dry run ok, {processed} bytes)")
    except Exception as exc:  # every failure becomes a diagnosis
        return diagnose(exc)
