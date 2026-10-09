"""Credential health check: diagnoses map failures to the credential to fix."""

from __future__ import annotations

from unittest.mock import MagicMock

from google.api_core import exceptions as gexc
from google.auth import exceptions as auth_exc
from google.cloud import bigquery

from proptech import healthcheck


def test_healthy_client() -> None:
    client = MagicMock(spec=bigquery.Client)
    client.project = "proptech-housing"
    client.list_datasets.return_value = iter([])
    client.query.return_value = MagicMock(total_bytes_processed=0)
    health = healthcheck.check("proptech-housing", "US", client=client)
    assert health.ok
    assert client.query.call_args.kwargs["job_config"].dry_run is True


def test_revoked_key_points_at_the_credential() -> None:
    client = MagicMock(spec=bigquery.Client)
    client.list_datasets.side_effect = auth_exc.RefreshError("invalid_grant: Invalid JWT Signature.")
    health = healthcheck.check("p", "US", client=client)
    assert not health.ok
    assert "GCP_SA_KEY" in health.fix


def test_missing_roles_and_disabled_api_are_told_apart() -> None:
    denied = healthcheck.diagnose(gexc.Forbidden("Access Denied: User does not have bigquery.jobs.create"))
    disabled = healthcheck.diagnose(gexc.Forbidden("BigQuery API has not been used in project 1 before"))
    assert "roles/bigquery" in denied.fix
    assert "Enable the BigQuery API" in disabled.fix


def test_unauthenticated() -> None:
    assert "GCP_SA_KEY" in healthcheck.diagnose(gexc.Unauthorized("401")).fix


def test_cli_writes_diagnosis_on_failure(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from typer.testing import CliRunner

    from proptech.cli import app

    monkeypatch.setenv("PROPTECH_TARGET", "bigquery")
    monkeypatch.setenv("PROPTECH_BQ_PROJECT", "proptech-housing")
    monkeypatch.setattr(
        healthcheck,
        "check",
        lambda *_a, **_k: healthcheck.Health(False, "credentials rejected: x", "renew it"),
    )
    out = tmp_path / "diag.md"
    result = CliRunner().invoke(app, ["--no-json-logs", "healthcheck", "--out", str(out)])
    assert result.exit_code == 1
    assert "renew it" in out.read_text()
