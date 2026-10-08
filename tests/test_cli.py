from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from proptech.cli import app
from proptech.settings import Settings

runner = CliRunner()


def _env(settings: Settings) -> dict[str, str]:
    return {
        "PROPTECH_TARGET": "duckdb",
        "PROPTECH_DUCKDB_PATH": str(settings.duckdb_path),
        "PROPTECH_RAW_ARCHIVE_URI": settings.raw_archive_uri,
        "PROPTECH_ZILLOW_BASE_URL": settings.zillow_base_url,
        "PROPTECH_PMMS_URL": settings.pmms_url,
        "PROPTECH_ACS_BASE_URL": settings.acs_base_url,
        "PROPTECH_RELAXED_VALIDATION": "true",
        "PROPTECH_ACS_YEARS": "2023,2024",
    }


def test_ingest_command_writes_summary(
    fixture_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    summary = tmp_path / "summary.json"
    result = runner.invoke(
        app,
        [
            "--no-json-logs",
            "ingest",
            "--only",
            "zhvi_metro_mid_all",
            "--summary-json",
            str(summary),
        ],
        env=_env(fixture_settings),
    )
    assert result.exit_code == 0, result.output
    assert "| `zhvi_metro_mid_all` | loaded |" in result.output
    payload = json.loads(summary.read_text(encoding="utf-8"))
    assert payload["failed"] == []
    assert {e["dataset_key"] for e in payload["entries"]} == {
        "zhvi_metro_mid_all",
        "pmms_weekly",
        "acs_median_household_income",
    }


def test_ingest_exits_non_zero_on_failure(fixture_settings: Settings, tmp_path: Path) -> None:
    env = _env(fixture_settings) | {"PROPTECH_ZILLOW_BASE_URL": (tmp_path / "nowhere").as_uri()}
    result = runner.invoke(
        app, ["--no-json-logs", "ingest", "--only", "zhvi_metro_mid_all", "--no-reference"], env=env
    )
    assert result.exit_code == 1


def test_dbt_sources_check_passes() -> None:
    result = runner.invoke(app, ["dbt-sources", "--check"])
    assert result.exit_code == 0, result.output


def test_settings_parse_comma_separated_years(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROPTECH_ACS_YEARS", "2022, 2024")
    assert Settings(_env_file=None).acs_years == [2022, 2024]
